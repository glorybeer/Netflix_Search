import os
import re
import json
import time
import requests
from urllib.parse import unquote

# 1. GitHub Secrets에서 API 키 로드
TMDB_API_KEY = os.environ.get('TMDB_API_KEY', '')
RAW_KMRB_API_KEY = os.environ.get('KMRB_API_KEY', '')

# 공공데이터포럼 키가 이중 인코딩되는 것 방지 (Decoded Key 확보)
KMRB_API_KEY = unquote(RAW_KMRB_API_KEY)

DATASET_FILE = 'kmrb_full_dataset.json'
PROGRESS_FILE = 'collection_progress.json'

# 회차당 수집할 페이지 수 (영화/TV 각 20페이지)
PAGES_PER_RUN = 20

# 차단 방지용 브라우저 User-Agent 설정
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json"
}

def load_json(filepath, default):
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ {filepath} 읽기 실패: {e}")
    return default

def save_json(filepath, data):
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def clean_title_text(title):
    """영등위 검색 성공률 향상을 위한 제목 특수문자 및 수식어 정제"""
    if not title:
        return ""
    # 괄호 안 텍스트 제거 (예: 매트릭스 (1999) -> 매트릭스)
    text = re.sub(r'\(.*?\)|\[.*?\]|\<.*?\>', '', title)
    # 특수문자 제거 및 공백 정제
    text = re.sub(r'[\:\-\_\~\!\@\#\$\%\^\&\*\=\+\;\,\.\?]', ' ', text)
    return text.strip()

def fetch_kmrb_rating(title):
    """영등위(KMRB) Open API 조회 (403 에러 방지 처리 완료)"""
    cleaned = clean_title_text(title)
    if not cleaned:
        cleaned = title

    # apis.data.go.kr 403 방지를 위해 serviceKey를 URL에 직접 바인딩
    url = "http://apis.data.go.kr/B551014/videoInfoService/getVideoInfoSearch"
    
    params = {
        "serviceKey": KMRB_API_KEY,
        "title": cleaned,
        "numOfRows": 1,
        "pageNo": 1,
        "_type": "json"
    }

    try:
        # requests가 키를 이중 인코딩하지 않도록 Request 생성
        req = requests.Request('GET', url, params=params, headers=HEADERS).prepare()
        
        # urllib에서 키가 다시 재인코딩되는 현상 보정
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

                return {
                    'theme': parse_val(item.get('theme')),
                    'sensuality': parse_val(item.get('sensuality')),
                    'violence': parse_val(item.get('violence')),
                    'dialogue': parse_val(item.get('dialogue')),
                    'horror': parse_val(item.get('horror')),
                    'drug': parse_val(item.get('drug')),
                    'imitation': parse_val(item.get('imitation'))
                }
    except Exception as e:
        print(f"  ⚠️ KMRB 조회 예외 발생 ({title}): {e}")

    # 조회 실패 시 기본값 0 세팅
    return {'theme': 0, 'sensuality': 0, 'violence': 0, 'dialogue': 0, 'horror': 0, 'drug': 0, 'imitation': 0}

def main():
    if not TMDB_API_KEY or not KMRB_API_KEY:
        print("❌ Error: GitHub Secrets에 TMDB_API_KEY 또는 KMRB_API_KEY가 설정되지 않았습니다.")
        return

    progress = load_json(PROGRESS_FILE, {'last_movie_page': 0, 'last_tv_page': 0})
    dataset = load_json(DATASET_FILE, [])

    existing_ids = {item['id'] for item in dataset}
    last_movie_p = progress.get('last_movie_page', 0)
    last_tv_p = progress.get('last_tv_page', 0)

    print(f"🚀 수집 시작 | 기존 누적 데이터: {len(dataset)}개")
    print(f"📍 현재 진행 상황 -> Movie: {last_movie_p}p / TV: {last_tv_p}p")

    # 1. Movie 카테고리 수집
    movie_target_end = last_movie_p + PAGES_PER_RUN
    print(f"\n🎬 [MOVIE] {last_movie_p + 1}p ~ {movie_target_end}p 수집 중...")
    
    for page in range(last_movie_p + 1, movie_target_end + 1):
        tmdb_url = f"https://api.themoviedb.org/3/discover/movie?api_key={TMDB_API_KEY}&with_watch_providers=8&watch_region=KR&language=ko-KR&page={page}"
        try:
            res = requests.get(tmdb_url, headers=HEADERS, timeout=5).json()
            results = res.get('results', [])
            if not results:
                print(f"  - [{page}p] 영화 수집 완료 (페이지 끝)")
                break

            for item in results:
                if item['id'] in existing_ids:
                    continue
                title = item.get('title')
                if title:
                    scores = fetch_kmrb_rating(title)
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
                    time.sleep(0.05)
            
            progress['last_movie_page'] = page
            save_json(DATASET_FILE, dataset)
            save_json(PROGRESS_FILE, progress)
            print(f"  -> [Movie {page}p 완료] 누적 데이터: {len(dataset)}개")

        except Exception as e:
            print(f"  ❌ Movie {page}p 처리 중 에러: {e}")
            break

    # 2. TV 카테고리 수집
    tv_target_end = last_tv_p + PAGES_PER_RUN
    print(f"\n📺 [TV] {last_tv_p + 1}p ~ {tv_target_end}p 수집 중...")
    
    for page in range(last_tv_p + 1, tv_target_end + 1):
        tmdb_url = f"https://api.themoviedb.org/3/discover/tv?api_key={TMDB_API_KEY}&with_watch_providers=8&watch_region=KR&language=ko-KR&page={page}"
        try:
            res = requests.get(tmdb_url, headers=HEADERS, timeout=5).json()
            results = res.get('results', [])
            if not results:
                print(f"  - [{page}p] TV 수집 완료 (페이지 끝)")
                break

            for item in results:
                if item['id'] in existing_ids:
                    continue
                title = item.get('name')
                if title:
                    scores = fetch_kmrb_rating(title)
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
                    time.sleep(0.05)

            progress['last_tv_page'] = page
            save_json(DATASET_FILE, dataset)
            save_json(PROGRESS_FILE, progress)
            print(f"  -> [TV {page}p 완료] 누적 데이터: {len(dataset)}개")

        except Exception as e:
            print(f"  ❌ TV {page}p 처리 중 에러: {e}")
            break

    print(f"\n✅ 이번 회차 완료! 총 누적 수집된 데이터: {len(dataset)}개")

if __name__ == "__main__":
    main()
