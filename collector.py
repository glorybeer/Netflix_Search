import os
import re
import json
import time
import requests
from urllib.parse import unquote

# 1. GitHub Secrets에서 API 키 로드
TMDB_API_KEY = os.environ.get('TMDB_API_KEY', '')
RAW_KMRB_API_KEY = os.environ.get('KMRB_API_KEY', '')

# 공공데이터포털 키는 "Decoding(일반 인증키)" 형태를 그대로 쓰는 것이 가장 안전합니다.
# 시크릿에 실수로 "Encoding" 키(문자열 안에 %2B, %3D 등이 이미 포함된 형태)가
# 들어있는 경우에만 한 번 unquote로 원래 형태(raw)로 되돌립니다.
# -> 이후에는 requests가 params를 통해 알아서 정확히 1회만 percent-encoding 하도록
#    맡기고, 수동으로 URL 문자열을 다시 만지는 로직은 완전히 제거했습니다.
#    (수동 replace('%25','%') 같은 처리가 오히려 서명을 깨뜨려 400을 유발할 수 있습니다.)
KMRB_API_KEY = unquote(RAW_KMRB_API_KEY) if RAW_KMRB_API_KEY else ''

DATASET_FILE = 'kmrb_full_dataset.json'
PROGRESS_FILE = 'collection_progress.json'

# 회차당 수집할 페이지 수 (영화/TV 각 20페이지)
PAGES_PER_RUN = 20

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json"
}


def log(msg):
    """실시간으로 GitHub Actions 콘솔에 출력을 강제 배출(flush)하는 함수"""
    print(msg, flush=True)


def load_json(filepath, default):
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            log(f"⚠️ {filepath} 읽기 실패: {e}")
    return default


def save_json(filepath, data):
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def clean_title_text(title):
    """영등위 검색 성공률 향상을 위한 제목 특수문자 및 수식어 정제"""
    if not title:
        return ""
    text = re.sub(r'\(.*?\)|\[.*?\]|\<.*?\>', '', title)
    text = re.sub(r'[\:\-\_\~\!\@\#\$\%\^\&\*\=\+\;\,\.\?]', ' ', text)
    return text.strip()


def fetch_kmrb_rating(title):
    """영등위(KMRB) Open API 조회 (HTTPS 적용 및 재시도 로직 강화)"""
    cleaned = clean_title_text(title)
    if not cleaned:
        cleaned = title

    url = "https://apis.data.go.kr/B551014/videoInfoService/getVideoInfoSearch"

    params = {
        "serviceKey": KMRB_API_KEY,
        "title": cleaned,
        "numOfRows": 1,
        "pageNo": 1,
        "_type": "json"
    }

    # 타임아웃 발생 시 최대 3회 재시도
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            # params를 그대로 넘겨서 requests가 정확히 1회만 인코딩하도록 함
            # (URL을 수동으로 재조립하지 않음 -> 서명 손상 방지)
            timeout_sec = 5 + (attempt - 1) * 3
            res = requests.get(url, params=params, headers=HEADERS, timeout=timeout_sec)

            if res.status_code == 200:
                try:
                    data = res.json()
                except ValueError:
                    # _type=json을 지원하지 않거나 XML로 응답이 온 경우
                    log(f"    ⚠️ KMRB 응답이 JSON이 아님 ({title}) - 본문: {res.text[:200]}")
                    return {'theme': 0, 'sensuality': 0, 'violence': 0, 'dialogue': 0,
                            'horror': 0, 'drug': 0, 'imitation': 0}, False

                header = data.get('response', {}).get('header', {})
                result_code = header.get('resultCode')
                if result_code not in (None, '00', 0):
                    # 게이트웨이/기관 API가 200으로 응답했지만 내부적으로 에러 코드를 실은 경우
                    log(f"    ⚠️ KMRB API 오류 ({title}) - code: {result_code}, "
                        f"msg: {header.get('resultMsg')}")
                    return {'theme': 0, 'sensuality': 0, 'violence': 0, 'dialogue': 0,
                            'horror': 0, 'drug': 0, 'imitation': 0}, False

                items = data.get('response', {}).get('body', {}).get('items', {}).get('item')

                if items:
                    item = items[0] if isinstance(items, list) else items

                    def parse_val(v):
                        if not v:
                            return 0
                        val_str = str(v).strip()
                        if val_str in ['3', '높음']:
                            return 3
                        if val_str in ['2', '다소높음']:
                            return 2
                        if val_str in ['1', '낮음']:
                            return 1
                        return 0

                    scores = {
                        'theme': parse_val(item.get('theme')),
                        'sensuality': parse_val(item.get('sensuality')),
                        'violence': parse_val(item.get('violence')),
                        'dialogue': parse_val(item.get('dialogue')),
                        'horror': parse_val(item.get('horror')),
                        'drug': parse_val(item.get('drug')),
                        'imitation': parse_val(item.get('imitation'))
                    }
                    return scores, True
                else:
                    return {'theme': 0, 'sensuality': 0, 'violence': 0, 'dialogue': 0,
                            'horror': 0, 'drug': 0, 'imitation': 0}, False
            else:
                # 실패 원인 진단을 위해 응답 본문을 함께 출력
                log(f"    ⚠️ KMRB HTTP {res.status_code} 오류 ({title}) - 응답: {res.text[:300]}")
                break
        except requests.exceptions.Timeout:
            if attempt < max_retries:
                log(f"    ⏳ KMRB 접속 지연 ({title}) - {attempt}회차 재시도 중...")
                time.sleep(1)
            else:
                log(f"    ❌ KMRB 타임아웃 초과 ({title}): 3회 재시도 실패")
        except Exception as e:
            log(f"    ⚠️ KMRB 예외 발생 ({title}): {e}")
            break

    return {'theme': 0, 'sensuality': 0, 'violence': 0, 'dialogue': 0, 'horror': 0,
            'drug': 0, 'imitation': 0}, False


def build_poster_url(poster_path):
    """TMDB 포스터 URL 생성 (기존 't500' 오타 수정 -> 't/p/w500')"""
    if not poster_path:
        return ''
    return f"https://image.tmdb.org/t/p/w500{poster_path}"


def main():
    log("==================================================")
    log("🚀 KMRB 넷플릭스 수집기 (HTTPS 및 재시도 보완 버전)")
    log("==================================================")

    if not TMDB_API_KEY or not KMRB_API_KEY:
        log("❌ Error: GitHub Secrets에 TMDB_API_KEY 또는 KMRB_API_KEY가 설정되지 않았습니다.")
        return

    progress = load_json(PROGRESS_FILE, {'last_movie_page': 0, 'last_tv_page': 0})
    dataset = load_json(DATASET_FILE, [])

    existing_ids = {item['id'] for item in dataset}
    last_movie_p = progress.get('last_movie_page', 0)
    last_tv_p = progress.get('last_tv_page', 0)

    log(f"📊 현재 누적 수집 작품 수: {len(dataset)}개")
    log(f"📍 진행 위치 기록 -> MOVIE: {last_movie_p}p / TV: {last_tv_p}p")

    # 1. MOVIE 수집
    movie_target_end = last_movie_p + PAGES_PER_RUN
    log(f"\n🎬 [MOVIE] 카테고리 수집 시작 ({last_movie_p + 1}p ~ {movie_target_end}p)")
    log("-" * 50)

    for page in range(last_movie_p + 1, movie_target_end + 1):
        tmdb_url = (f"https://api.themoviedb.org/3/discover/movie?api_key={TMDB_API_KEY}"
                    f"&with_watch_providers=8&watch_region=KR&language=ko-KR&page={page}")
        try:
            res = requests.get(tmdb_url, headers=HEADERS, timeout=5).json()
            results = res.get('results', [])
            if not results:
                log(f"  - [{page}p] 영화 수집 완료 (더 이상 데이터 없음)")
                break

            log(f"\n📄 MOVIE {page}/{movie_target_end} 페이지 처리 중 (작품 수: {len(results)}개)")
            new_added_in_page = 0

            for idx, item in enumerate(results, 1):
                if item['id'] in existing_ids:
                    log(f"  [{idx}/{len(results)}] ⏩ 중복 건너뜀: {item.get('title')}")
                    continue

                title = item.get('title')
                if title:
                    scores, is_matched = fetch_kmrb_rating(title)
                    release_date = item.get('release_date', '')

                    dataset.append({
                        'id': item['id'],
                        'title': title,
                        'year': release_date[:4] if release_date else '미상',
                        'poster': build_poster_url(item.get('poster_path')),
                        'description': item.get('overview', ''),
                        'scores': scores
                    })
                    existing_ids.add(item['id'])
                    new_added_in_page += 1

                    match_status = "✅ KMRB 매칭 성공" if is_matched else "⚠️ 기본값(0) 적용"
                    score_str = f"주제:{scores['theme']} 선정:{scores['sensuality']} 폭력:{scores['violence']}"
                    log(f"  [{idx}/{len(results)}] 🎬 '{title}' ({match_status}) -> {score_str} "
                        f"| (총 누적: {len(dataset)}개)")
                    time.sleep(0.1)  # 서버 차단 방지 간격

            progress['last_movie_page'] = page
            save_json(DATASET_FILE, dataset)
            save_json(PROGRESS_FILE, progress)
            log(f"  --> MOVIE {page}p 저장 완료 (+{new_added_in_page}개 추가됨)")

        except Exception as e:
            log(f"  ❌ MOVIE {page}p 처리 중 에러 발생: {e}")
            break

    # 2. TV 수집
    tv_target_end = last_tv_p + PAGES_PER_RUN
    log(f"\n📺 [TV] 카테고리 수집 시작 ({last_tv_p + 1}p ~ {tv_target_end}p)")
    log("-" * 50)

    for page in range(last_tv_p + 1, tv_target_end + 1):
        tmdb_url = (f"https://api.themoviedb.org/3/discover/tv?api_key={TMDB_API_KEY}"
                    f"&with_watch_providers=8&watch_region=KR&language=ko-KR&page={page}")
        try:
            res = requests.get(tmdb_url, headers=HEADERS, timeout=5).json()
            results = res.get('results', [])
            if not results:
                log(f"  - [{page}p] TV 수집 완료 (더 이상 데이터 없음)")
                break

            log(f"\n📄 TV {page}/{tv_target_end} 페이지 처리 중 (작품 수: {len(results)}개)")
            new_added_in_page = 0

            for idx, item in enumerate(results, 1):
                if item['id'] in existing_ids:
                    log(f"  [{idx}/{len(results)}] ⏩ 중복 건너뜀: {item.get('name')}")
                    continue

                title = item.get('name')
                if title:
                    scores, is_matched = fetch_kmrb_rating(title)
                    first_air_date = item.get('first_air_date', '')

                    dataset.append({
                        'id': item['id'],
                        'title': title,
                        'year': first_air_date[:4] if first_air_date else '미상',
                        'poster': build_poster_url(item.get('poster_path')),
                        'description': item.get('overview', ''),
                        'scores': scores
                    })
                    existing_ids.add(item['id'])
                    new_added_in_page += 1

                    match_status = "✅ KMRB 매칭 성공" if is_matched else "⚠️ 기본값(0) 적용"
                    score_str = f"주제:{scores['theme']} 선정:{scores['sensuality']} 폭력:{scores['violence']}"
                    log(f"  [{idx}/{len(results)}] 📺 '{title}' ({match_status}) -> {score_str} "
                        f"| (총 누적: {len(dataset)}개)")
                    time.sleep(0.1)

            progress['last_tv_page'] = page
            save_json(DATASET_FILE, dataset)
            save_json(PROGRESS_FILE, progress)
            log(f"  --> TV {page}p 저장 완료 (+{new_added_in_page}개 추가됨)")

        except Exception as e:
            log(f"  ❌ TV {page}p 처리 중 에러 발생: {e}")
            break

    log("\n==================================================")
    log(f"🎉 수집 회차 완료! 최종 저장된 데이터: {len(dataset)}개")
    log("==================================================")


if __name__ == "__main__":
    main()
