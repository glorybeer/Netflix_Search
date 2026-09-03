#!/usr/bin/env python3
"""
KMRB Netflix 데이터 자동 수집 스크립트
- TMDB API에서 영화/TV 데이터 수집
- 영등위(KMRB) Open API에서 7대 성향 정보 수집
- 이어서 수집(Resume) 지원
"""

import os
import json
import requests
import re
import time
from pathlib import Path
from datetime import datetime
from urllib.parse import quote

# API 키 로드
TMDB_API_KEY = os.environ.get('TMDB_API_KEY')
KMRB_API_KEY = os.environ.get('KMRB_API_KEY')

# 수집 설정
MOVIES_PER_RUN = 30
TV_PER_RUN = 30
PROGRESS_FILE = 'collection_progress.json'
DATASET_FILE = 'kmrb_full_dataset.json'

# API 엔드포인트
TMDB_BASE = 'https://api.themoviedb.org/3'
KMRB_BASE = 'https://www.kmrb.or.kr/OpenAPI/openapi'

class DataCollector:
    def __init__(self):
        self.tmdb_session = requests.Session()
        self.kmrb_session = requests.Session()
        self.dataset = {'movies': [], 'tv': []}
        self.progress = {
            'last_movie_page': 0,
            'last_tv_page': 0,
            'movie_count': 0,
            'tv_count': 0,
            'last_updated': datetime.now().isoformat()
        }
        self.load_existing_data()
    
    def load_existing_data(self):
        """기존 데이터셋과 진행 상황 로드"""
        if Path(DATASET_FILE).exists():
            with open(DATASET_FILE, 'r', encoding='utf-8') as f:
                self.dataset = json.load(f)
        
        if Path(PROGRESS_FILE).exists():
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                self.progress = json.load(f)
        
        print(f"✅ 기존 데이터 로드 완료")
        print(f"   - 영화: {len(self.dataset.get('movies', []))}개")
        print(f"   - TV: {len(self.dataset.get('tv', []))}개")
    
    def sanitize_title(self, title):
        """검색용 제목 정제 (특수문자 제거, 시즌 정보 제거)"""
        # 시즌 정보 제거 (예: "Title Season 1" -> "Title")
        title = re.sub(r'\s*[Ss]eason\s+\d+', '', title)
        title = re.sub(r'\s*[Ss]1', '', title)
        
        # 괄호 안의 정보 제거 (예: "Title (2023)" -> "Title")
        title = re.sub(r'\s*\([^)]*\)', '', title)
        
        # 특수문자 제거
        title = re.sub(r'[^\w\s가-힣]', '', title)
        
        # 연속된 공백 제거
        title = ' '.join(title.split())
        
        return title.strip()
    
    def fetch_tmdb_movies(self, page):
        """TMDB에서 영화 데이터 수집"""
        url = f"{TMDB_BASE}/discover/movie"
        params = {
            'api_key': TMDB_API_KEY,
            'page': page,
            'sort_by': 'popularity.desc',
            'language': 'ko-KR',
            'region': 'KR'
        }
        
        try:
            response = self.tmdb_session.get(url, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"❌ TMDB 영화 API 오류 (페이지 {page}): {e}")
            return None
    
    def fetch_tmdb_tv(self, page):
        """TMDB에서 TV 데이터 수집"""
        url = f"{TMDB_BASE}/discover/tv"
        params = {
            'api_key': TMDB_API_KEY,
            'page': page,
            'sort_by': 'popularity.desc',
            'language': 'ko-KR',
            'region': 'KR'
        }
        
        try:
            response = self.tmdb_session.get(url, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"❌ TMDB TV API 오류 (페이지 {page}): {e}")
            return None
    
    def fetch_kmrb_rating(self, title):
        """영등위(KMRB) Open API에서 성향 정보 수집"""
        sanitized_title = self.sanitize_title(title)
        
        url = f"{KMRB_BASE}/searchTitleInfo"
        params = {
            'title': sanitized_title,
            'apiKey': KMRB_API_KEY,
            'returnType': 'json'
        }
        
        try:
            response = self.kmrb_session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if data.get('resultCode') == '00' and data.get('data'):
                result = data['data'][0]
                
                # 7대 성향 파싱 (0~3 수치)
                return {
                    'grade': result.get('gradeName', 'All'),
                    'themes': int(result.get('themes', -1)),
                    'sexuality': int(result.get('sexuality', -1)),
                    'violence': int(result.get('violence', -1)),
                    'language': int(result.get('language', -1)),
                    'fear': int(result.get('fear', -1)),
                    'drugs': int(result.get('drugs', -1)),
                    'imitation': int(result.get('imitation', -1))
                }
        except Exception as e:
            print(f"⚠️  KMRB API 오류 ({title}): {e}")
        
        return None
    
    def collect_movies(self):
        """영화 데이터 수집"""
        start_page = self.progress.get('last_movie_page', 0) + 1
        end_page = start_page + MOVIES_PER_RUN
        
        print(f"\n🎬 영화 수집 시작: {start_page}~{end_page-1} 페이지")
        
        for page in range(start_page, end_page):
            print(f"📄 영화 페이지 {page} 처리 중...")
            
            result = self.fetch_tmdb_movies(page)
            if not result:
                break
            
            for item in result.get('results', []):
                movie_id = item.get('id')
                title = item.get('title', '제목없음')
                
                # 중복 확인
                if any(m['id'] == movie_id for m in self.dataset['movies']):
                    continue
                
                # 영등위 정보 수집
                kmrb_info = self.fetch_kmrb_rating(title)
                
                movie_data = {
                    'id': movie_id,
                    'type': 'movie',
                    'title': title,
                    'release_date': item.get('release_date', ''),
                    'poster_path': item.get('poster_path', ''),
                    'overview': item.get('overview', ''),
                    'genres': item.get('genre_ids', []),
                    'vote_average': item.get('vote_average', 0),
                    'popularity': item.get('popularity', 0),
                    'kmrb': kmrb_info or {}
                }
                
                self.dataset['movies'].append(movie_data)
                print(f"   ✅ {title} - 등급: {kmrb_info.get('grade', 'N/A') if kmrb_info else 'N/A'}")
                
                # API 레이트 제한 회피
                time.sleep(0.1)
            
            self.progress['last_movie_page'] = page
            self.save_progress()
            time.sleep(1)
        
        print(f"✅ 영화 수집 완료: 총 {len(self.dataset['movies'])}개")
    
    def collect_tv(self):
        """TV 데이터 수집"""
        start_page = self.progress.get('last_tv_page', 0) + 1
        end_page = start_page + TV_PER_RUN
        
        print(f"\n📺 TV 수집 시작: {start_page}~{end_page-1} 페이지")
        
        for page in range(start_page, end_page):
            print(f"📄 TV 페이지 {page} 처리 중...")
            
            result = self.fetch_tmdb_tv(page)
            if not result:
                break
            
            for item in result.get('results', []):
                tv_id = item.get('id')
                title = item.get('name', '제목없음')
                
                # 중복 확인
                if any(t['id'] == tv_id for t in self.dataset['tv']):
                    continue
                
                # 영등위 정보 수집
                kmrb_info = self.fetch_kmrb_rating(title)
                
                tv_data = {
                    'id': tv_id,
                    'type': 'tv',
                    'title': title,
                    'first_air_date': item.get('first_air_date', ''),
                    'poster_path': item.get('poster_path', ''),
                    'overview': item.get('overview', ''),
                    'genres': item.get('genre_ids', []),
                    'vote_average': item.get('vote_average', 0),
                    'popularity': item.get('popularity', 0),
                    'kmrb': kmrb_info or {}
                }
                
                self.dataset['tv'].append(tv_data)
                print(f"   ✅ {title} - 등급: {kmrb_info.get('grade', 'N/A') if kmrb_info else 'N/A'}")
                
                # API 레이트 제한 회피
                time.sleep(0.1)
            
            self.progress['last_tv_page'] = page
            self.save_progress()
            time.sleep(1)
        
        print(f"✅ TV 수집 완료: 총 {len(self.dataset['tv'])}개")
    
    def save_progress(self):
        """진행 상황 저장"""
        self.progress['movie_count'] = len(self.dataset['movies'])
        self.progress['tv_count'] = len(self.dataset['tv'])
        self.progress['last_updated'] = datetime.now().isoformat()
        
        with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.progress, f, ensure_ascii=False, indent=2)
    
    def save_dataset(self):
        """최종 데이터셋 저장"""
        with open(DATASET_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.dataset, f, ensure_ascii=False, indent=2)
        print(f"\n💾 데이터셋 저장 완료: {DATASET_FILE}")
    
    def run(self):
        """전체 수집 프로세스 실행"""
        print("=" * 60)
        print("🚀 KMRB Netflix 데이터 자동 수집 시작")
        print(f"   시작 시간: {datetime.now().isoformat()}")
        print("=" * 60)
        
        try:
            if not TMDB_API_KEY or not KMRB_API_KEY:
                print("❌ 오류: API 키가 설정되지 않았습니다.")
                print("   - TMDB_API_KEY")
                print("   - KMRB_API_KEY")
                return False
            
            self.collect_movies()
            self.collect_tv()
            self.save_dataset()
            self.save_progress()
            
            print("\n" + "=" * 60)
            print("✅ 데이터 수집 완료!")
            print(f"   - 총 영화: {len(self.dataset['movies'])}개")
            print(f"   - 총 TV: {len(self.dataset['tv'])}개")
            print(f"   - 종료 시간: {datetime.now().isoformat()}")
            print("=" * 60)
            
            return True
        
        except Exception as e:
            print(f"❌ 수집 중 오류 발생: {e}")
            return False

if __name__ == '__main__':
    collector = DataCollector()
    success = collector.run()
    exit(0 if success else 1)
