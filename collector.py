import os
import re
import json
import time
import requests
from urllib.parse import unquote

# 1. GitHub Secrets에서 API 키 로드
TMDB_API_KEY = os.environ.get('TMDB_API_KEY', '')
RAW_KMRB_API_KEY = os.environ.get('KMRB_API_KEY', '')

# 공공데이터포럼 키 이중 인코딩 방지 (Decoded Key 확보)
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
    """영등위(KMRB) Open API 조회 (403 에러 방지 및 상세 로그 출력)"""
    cleaned = clean_title_text(title)
    if not cleaned:
        cleaned = title

    url = "http://apis.data.go.kr/B551014/videoInfoService/getVideoInfoSearch"
    
    params = {
        "serviceKey": KMRB_API_KEY,
        "title": cleaned,
        "numOfRows": 1,
        "pageNo": 1,
        "_type": "json"
    }

    try:
        req = requests.Request('GET', url, params=params, headers=HEADERS).prepare()
        req.url = req.url.replace('%25', '%')
        
        session = requests.Session()
        res = session.send(req, timeout=5)

        if res.status_code == 200:
            data = res.json()
            items = data.get('response', {}).get('body', {}).get('items', {}).get('item')
            
            if items:
                item = items[0] if isinstance(items, list) else items
                
                def parse_val(v):
                    if not v: return 0
                    val_str = str(v).strip()
                    if val_str in ['3', '높음']: return 3
                    if val_str in ['2', '다소높음']: return 2
                    if val_str in ['1', '낮음']: return 1
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
                return {'theme': 0, 'sensuality': 0, 'violence': 0, 'dialogue': 0, 'horror': 0, 'drug': 0, 'imitation': 0}, False
        else:
            log(f"    ⚠️ KMRB HTTP {res.status_code} 오류 ({title})")
    except Exception as e:
        log(f"    ⚠️ KMRB 예외 발생 ({title}): {e}")

    return {'theme': 0, 'sensuality': 0, 'violence': 0, 'dialogue': 0, 'horror': 0, 'drug': 0, 'imitation': 0}, False

def main():
    log("==================================================")
    log("🚀 KMRB 넷플릭스 수집기 (실시간 진행 상황 모니터링)")
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
        tmdb_url = f"https://api.themoviedb.org/3/discover/movie?api_key={TMDB_API_KEY}&with_watch_providers=8&watch_region=KR&language=ko-KR&page={page}"
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
                        'poster': f"https://image.tmdb.org/t500{item.get('poster_path')}" if item.get('poster_path') else '',
                        'description': item.get('overview', ''),
                        'scores': scores
                    })
                    existing_ids.add(item['id'])
                    new_added_in_page += 1

                    match_status = "✅ KMRB 매칭 성공" if is_matched else "⚠️ 기본값(0) 적용"
                    score_str = f"주제:{scores['theme']} 선정:{scores['sensuality']} 폭력:{scores['violence']}"
                    log(f"  [{idx}/{len(results)}] 🎬 '{title}' ({match_status}) -> {score_str} | (총 누적: {len(dataset)}개)")
                    time.sleep(0.05)
            
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
        tmdb_url = f"https://api.themoviedb.org/3/discover/tv?api_key={TMDB_API_KEY}&with_watch_providers=8&watch_region=KR&language=ko-KR&page={page}"
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
                        'poster': f"https://image.tmdb.org/t500{item.get('poster_path')}" if item.get('poster_path') else '',
                        'description': item.get('overview', ''),
                        'scores': scores
                    })
                    existing_ids.add(item['id'])
                    new_added_in_page += 1

                    match_status = "✅ KMRB 매칭 성공" if is_matched else "⚠️ 기본값(0) 적용"
                    score_str = f"주제:{scores['theme']} 선정:{scores['sensuality']} 폭력:{scores['violence']}"
                    log(f"  [{idx}/{len(results)}] 📺 '{title}' ({match_status}) -> {score_str} | (총 누적: {len(dataset)}개)")
                    time.sleep(0.05)

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
