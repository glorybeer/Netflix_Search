#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
KMRB Netflix 데이터 자동 수집 스크립트
- TMDB API에서 영화/TV 데이터 수집
- 영등위(KMRB) data.go.kr API에서 성향 데이터 매칭
- 이어서 수집(Resume) 지원
- 정제된 JSON 출력
"""

import os
import json
import time
import re
from datetime import datetime
import requests
from typing import Dict, List, Any, Optional, Tuple

# ======================== 설정 ========================
TMDB_API_KEY = os.environ.get('TMDB_API_KEY')
KMRB_API_KEY = os.environ.get('KMRB_API_KEY')

TMDB_BASE_URL = "https://api.themoviedb.org/3"
KMRB_BASE_URL = "https://apis.data.go.kr/B551008/video_v2/video_search_v2"

OUTPUT_FILE = "kmrb_full_dataset.json"
PROGRESS_FILE = "collection_progress.json"

MOVIES_PER_RUN = 30  # 한 번 실행마다 영화 페이지 수
TV_PER_RUN = 30      # 한 번 실행마다 TV 페이지 수

# KMRB 7대 성향 지표
KMRB_INDICATORS = {
    "주제": "theme",
    "선정성": "sexuality",
    "폭력성": "violence",
    "대사": "language",
    "공포": "fear",
    "약물": "drug",
    "모방위험": "imitation"
}

# 한국 연령 등급 매핑
AGE_RATINGS = {
    "전체이용가": "ALL",
    "12세이용가": "12",
    "15세이용가": "15",
    "18세이용가": "18"
}


class KMRBCollector:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self.dataset = []
        self.progress = self.load_progress()
    
    def load_progress(self) -> Dict[str, Any]:
        """진행 상황 불러오기"""
        if os.path.exists(PROGRESS_FILE):
            try:
                with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"⚠️  진행 파일 로드 실패: {e}")
        
        return {
            "last_movie_page": 0,
            "last_tv_page": 0,
            "total_movies": 0,
            "total_tv": 0,
            "last_updated": datetime.now().isoformat()
        }
    
    def save_progress(self):
        """진행 상황 저장"""
        self.progress['last_updated'] = datetime.now().isoformat()
        with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.progress, f, ensure_ascii=False, indent=2)
    
    def save_dataset(self):
        """데이터셋 저장"""
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.dataset, f, ensure_ascii=False, indent=2)
        print(f"✅ 데이터셋 저장 완료: {len(self.dataset)}개 항목")
    
    def load_existing_dataset(self) -> List[Dict]:
        """기존 데이터셋 불러오기"""
        if os.path.exists(OUTPUT_FILE):
            try:
                with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"⚠️  기존 데이터셋 로드 실패: {e}")
        return []
    
    def sanitize_title(self, title: str) -> str:
        """제목 정제: 특수문자, 시즌 번호 제거"""
        # 시즌 정보 제거 (예: "Show Name Season 1" → "Show Name")
        title = re.sub(r'\s+[Ss]eason\s+\d+', '', title)
        title = re.sub(r'\s+시즌\s+\d+', '', title)
        
        # 괄호 안 정보 제거 (예: "Movie (2020)" → "Movie")
        title = re.sub(r'\s*\([^)]*\)\s*', ' ', title)
        
        # 특수문자 정제 (영문, 한글, 숫자만 유지)
        title = re.sub(r'[^\w\s가-힣]', ' ', title)
        
        # 연속 공백 제거
        title = re.sub(r'\s+', ' ', title).strip()
        
        return title
    
    def search_kmrb(self, title: str, media_type: str = "movie") -> Optional[Dict[str, Any]]:
        """영등위 data.go.kr API에서 제목으로 검색"""
        try:
            sanitized_title = self.sanitize_title(title)
            
            # data.go.kr KMRB API 요청
            params = {
                'serviceKey': KMRB_API_KEY,
                'pageNo': 1,
                'numOfRows': 1,
                'title': sanitized_title,
                'type': 'json'
            }
            
            response = self.session.get(KMRB_BASE_URL, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # API 응답 파싱
            if data.get('response', {}).get('body', {}).get('items'):
                items = data['response']['body']['items']
                
                if isinstance(items, list) and len(items) > 0:
                    result = items[0]
                else:
                    result = items
                
                # KMRB 지표 파싱
                indicators = {}
                for korean_name, english_name in KMRB_INDICATORS.items():
                    # API 응답에서 해당 지표 찾기
                    score_key = korean_name  # 예: "주제", "선정성"
                    score = result.get(score_key)
                    
                    if score is not None:
                        try:
                            # 숫자로 변환 (0-3 범위)
                            score_int = int(score) if score != '-1' else -1
                            indicators[english_name] = max(-1, min(3, score_int))
                        except (ValueError, TypeError):
                            indicators[english_name] = -1
                    else:
                        indicators[english_name] = -1
                
                # 연령등급 파싱
                rating_str = result.get('rating', '전체이용가')
                age_rating = AGE_RATINGS.get(rating_str, 'ALL')
                
                return {
                    "kmrb_id": result.get('rtNo'),
                    "rating": age_rating,
                    "indicators": indicators
                }
            
            return None
        
        except Exception as e:
            print(f"⚠️  KMRB 검색 실패 ({title}): {e}")
            return None
    
    def fetch_tmdb_movies(self, page: int) -> Tuple[List[Dict], bool]:
        """TMDB에서 영화 페이지 수집"""
        try:
            url = f"{TMDB_BASE_URL}/movie/popular"
            params = {
                "api_key": TMDB_API_KEY,
                "page": page,
                "region": "KR",
                "language": "ko-KR"
            }
            
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            movies = []
            for item in data.get('results', []):
                if item.get('poster_path') and item.get('title'):
                    movies.append({
                        'tmdb_id': item['id'],
                        'title': item['title'],
                        'original_title': item.get('original_title', ''),
                        'release_date': item.get('release_date', ''),
                        'overview': item.get('overview', ''),
                        'poster_path': item.get('poster_path', ''),
                        'vote_average': item.get('vote_average', 0),
                        'genre_ids': item.get('genre_ids', []),
                        'media_type': 'movie'
                    })
            
            has_next = data.get('page', 0) < data.get('total_pages', 0)
            return movies, has_next
        
        except Exception as e:
            print(f"❌ TMDB 영화 수집 실패 (페이지 {page}): {e}")
            return [], False
    
    def fetch_tmdb_tv(self, page: int) -> Tuple[List[Dict], bool]:
        """TMDB에서 TV 페이지 수집"""
        try:
            url = f"{TMDB_BASE_URL}/tv/popular"
            params = {
                "api_key": TMDB_API_KEY,
                "page": page,
                "language": "ko-KR"
            }
            
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            tv_shows = []
            for item in data.get('results', []):
                if item.get('poster_path') and item.get('name'):
                    tv_shows.append({
                        'tmdb_id': item['id'],
                        'title': item['name'],
                        'original_title': item.get('original_name', ''),
                        'first_air_date': item.get('first_air_date', ''),
                        'overview': item.get('overview', ''),
                        'poster_path': item.get('poster_path', ''),
                        'vote_average': item.get('vote_average', 0),
                        'genre_ids': item.get('genre_ids', []),
                        'media_type': 'tv'
                    })
            
            has_next = data.get('page', 0) < data.get('total_pages', 0)
            return tv_shows, has_next
        
        except Exception as e:
            print(f"❌ TMDB TV 수집 실패 (페이지 {page}): {e}")
            return [], False
    
    def enrich_with_kmrb(self, items: List[Dict]) -> List[Dict]:
        """각 항목에 KMRB 데이터 추가"""
        enriched = []
        for item in items:
            kmrb_data = self.search_kmrb(item['title'], item['media_type'])
            
            if kmrb_data:
                item.update(kmrb_data)
            else:
                item.update({
                    "kmrb_id": None,
                    "rating": "ALL",
                    "indicators": {k: -1 for k in KMRB_INDICATORS.values()}
                })
            
            enriched.append(item)
            time.sleep(0.1)  # API 레이트 제한 방지
        
        return enriched
    
    def collect(self):
        """메인 수집 함수"""
        print("🚀 KMRB Netflix 데이터 수집 시작")
        print(f"📅 시간: {datetime.now().isoformat()}")
        print()
        
        # 기존 데이터 불러오기
        self.dataset = self.load_existing_dataset()
        print(f"📦 기존 데이터: {len(self.dataset)}개 항목")
        
        # 환경변수로 재개 포인트 override 가능
        start_movie_page = int(os.environ.get('RESUME_MOVIES') or self.progress['last_movie_page']) + 1
        start_tv_page = int(os.environ.get('RESUME_TV') or self.progress['last_tv_page']) + 1
        
        print(f"📍 재개 포인트: 영화 페이지 {start_movie_page}, TV 페이지 {start_tv_page}")
        print()
        
        # ========== 영화 수집 ==========
        print("🎬 영화 데이터 수집 중...")
        movies_collected = 0
        
        for page in range(start_movie_page, start_movie_page + MOVIES_PER_RUN):
            print(f"  📄 영화 페이지 {page} 수집 중...", end=" ")
            movies, has_next = self.fetch_tmdb_movies(page)
            
            if movies:
                movies = self.enrich_with_kmrb(movies)
                self.dataset.extend(movies)
                movies_collected += len(movies)
                print(f"✅ ({len(movies)}개)")
                self.progress['last_movie_page'] = page
                self.save_progress()
            else:
                print("❌ 실패")
            
            if not has_next:
                print(f"  🏁 영화 마지막 페이지 도달 (페이지 {page})")
                break
            
            time.sleep(0.5)  # API 레이트 제한
        
        print(f"✅ 영화 수집 완료: {movies_collected}개")
        print()
        
        # ========== TV 수집 ==========
        print("📺 TV 데이터 수집 중...")
        tv_collected = 0
        
        for page in range(start_tv_page, start_tv_page + TV_PER_RUN):
            print(f"  📄 TV 페이지 {page} 수집 중...", end=" ")
            tv_shows, has_next = self.fetch_tmdb_tv(page)
            
            if tv_shows:
                tv_shows = self.enrich_with_kmrb(tv_shows)
                self.dataset.extend(tv_shows)
                tv_collected += len(tv_shows)
                print(f"✅ ({len(tv_shows)}개)")
                self.progress['last_tv_page'] = page
                self.save_progress()
            else:
                print("❌ 실패")
            
            if not has_next:
                print(f"  🏁 TV 마지막 페이지 도달 (페이지 {page})")
                break
            
            time.sleep(0.5)  # API 레이트 제한
        
        print(f"✅ TV 수집 완료: {tv_collected}개")
        print()
        
        # ========== 최종 저장 ==========
        self.progress['total_movies'] = len([x for x in self.dataset if x['media_type'] == 'movie'])
        self.progress['total_tv'] = len([x for x in self.dataset if x['media_type'] == 'tv'])
        
        self.save_dataset()
        self.save_progress()
        
        print("="*50)
        print("📊 수집 완료 통계")
        print("="*50)
        print(f"총 항목: {len(self.dataset)}")
        print(f"  - 영화: {self.progress['total_movies']}")
        print(f"  - TV: {self.progress['total_tv']}")
        print(f"이번 회차: {movies_collected + tv_collected}개 추가")
        print(f"마지막 업데이트: {self.progress['last_updated']}")
        print("="*50)


if __name__ == "__main__":
    if not TMDB_API_KEY:
        print("❌ 에러: TMDB_API_KEY 환경변수가 설정되지 않았습니다")
        exit(1)
    
    if not KMRB_API_KEY:
        print("❌ 에러: KMRB_API_KEY 환경변수가 설정되지 않았습니다")
        exit(1)
    
    collector = KMRBCollector()
    collector.collect()
