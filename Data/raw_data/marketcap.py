import pandas as pd
import yfinance as yf
import logging
from pathlib import Path
from datetime import datetime, timedelta
from tqdm import tqdm

def get_market_cap(ticker: str, start_date: str, end_date: str) -> tuple:
    """
    특정 기간의 종목 시가총액 정보를 가져옵니다.
    
    Args:
        ticker: 종목 코드
        start_date: 시작일 (YYYY-MM-DD)
        end_date: 종료일 (YYYY-MM-DD)
        
    Returns:
        tuple: (ticker, market_cap, date) 또는 오류 시 (ticker, None, None)
    """
    try:
        stock = yf.Ticker(ticker)
        # 해당 기간의 데이터 조회
        hist = stock.history(start=start_date, end=end_date)
        
        if len(hist) > 0:
            # 첫 번째 유효한 거래일의 데이터 사용
            first_valid_date = hist.index[0]
            close_price = hist.iloc[0]['Close']
            
            # 주식수 정보 가져오기
            shares = stock.info.get('sharesOutstanding', None)
            if shares:
                market_cap = close_price * shares
                return ticker, market_cap, first_valid_date
                
        return ticker, None, None
        
    except Exception as e:
        logging.warning(f"Failed to get market cap for {ticker}: {str(e)}")
        return ticker, None, None

def get_all_market_caps(target_date: str = '2018-01-01') -> pd.DataFrame:
    """
    특정 시점 기준 전체 종목의 시가총액을 추출합니다.
    
    Args:
        target_date: 목표 기준일 (YYYY-MM-DD)
    
    Returns:
        pd.DataFrame: 전체 종목의 시가총액 정보가 담긴 DataFrame
    """
    try:
        # returns_df.csv 파일 로드하여 전체 종목 리스트 추출
        returns_df = pd.read_csv(
            "/home/indi/codespace/ImagePortOpt/Data/processed/return_df.csv",
            index_col=0
        )
        
        available_tickers = returns_df.columns.tolist()
        logging.info(f"Total available tickers: {len(available_tickers)}")
        
        # 날짜 범위 설정 (목표일 전후 5일)
        target_date = datetime.strptime(target_date, '%Y-%m-%d')
        start_date = (target_date - timedelta(days=10)).strftime('%Y-%m-%d')
        end_date = (target_date + timedelta(days=1)).strftime('%Y-%m-%d')
        
        logging.info(f"Fetching market caps between {start_date} and {end_date}")
        
        # 순차적으로 시가총액 정보 가져오기
        market_caps = []
        for ticker in tqdm(available_tickers, desc="Fetching market caps"):
            ticker, cap, date = get_market_cap(ticker, start_date, end_date)
            if cap is not None:
                market_caps.append((ticker, cap, date))
                logging.info(f"Successfully fetched {ticker}: {cap:,.0f} ({date})")
            else:
                logging.warning(f"No market cap data for {ticker}")
        
        if not market_caps:
            raise ValueError("No market cap data found for any ticker")
        
        # 시가총액 순으로 정렬
        market_caps.sort(key=lambda x: x[1], reverse=True)
        
        # DataFrame 생성 및 저장
        market_cap_df = pd.DataFrame(market_caps, columns=['Symbol', 'MarketCap', 'Date'])
        market_cap_df = market_cap_df.sort_values('MarketCap', ascending=False)
        
        # 저장 경로 설정 및 생성
        save_dir = Path("/home/indi/codespace/ImagePortOpt/TS_Model/data")
        save_dir.mkdir(parents=True, exist_ok=True)
        
        # 시가총액 정보 저장
        filename = f"market_caps_{target_date.strftime('%Y%m%d')}.csv"
        market_cap_df.to_csv(save_dir / filename, index=False)
        
        logging.info(f"시가총액 정보 추출 완료")
        logging.info(f"전체 종목 수: {len(market_cap_df)}")
        logging.info(f"결과 저장 경로: {save_dir / filename}")
        
        # 시가총액 정보 출력
        print("\n상위 10개 종목 시가총액 정보:")
        print(market_cap_df.head(10).to_string())
        
        return market_cap_df
        
    except Exception as e:
        logging.error(f"시가총액 정보 추출 중 오류 발생: {str(e)}")
        raise

if __name__ == "__main__":
    # 로깅 설정
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    try:
        # 2018년 1월 1일 주변 거래일 기준 시가총액 정보 추출
        target_date = '2018-01-01'
        market_cap_df = get_all_market_caps(target_date)
        
    except Exception as e:
        logging.error(f"프로그램 실행 중 오류 발생: {str(e)}")