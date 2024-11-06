"""
데이터 전처리를 위한 모듈입니다.
주식 선택 및 포트폴리오 구성을 위한 데이터 처리 기능을 포함합니다.
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Set
import logging
from datetime import datetime
import os

class DataProcessor:
    """
    데이터 전처리를 위한 클래스입니다.
    """
    
    def __init__(self):
        """
        DataProcessor 초기화
        """
        self.logger = logging.getLogger(__name__)

    def select_stocks(self, 
                     data: pd.DataFrame, 
                     n_stocks: int, 
                     top: bool = True) -> pd.DataFrame:
        """
        주어진 기준에 따라 주식을 선택합니다.
        
        Args:
            data (pd.DataFrame): 앙상블 결과 데이터
            n_stocks (int): 선택할 주식 수
            top (bool): True면 상위 주식, False면 하위 주식 선택
            
        Returns:
            pd.DataFrame: 선택된 주식 데이터
        """
        return data.sort_values(
            ['investment_date', 'up_prob'], 
            ascending=[True, not top]
        ).groupby('investment_date').head(n_stocks)

    def create_stock_dict(self, 
                         selected_stocks: pd.DataFrame) -> Dict[datetime, List[str]]:
        """
        선택된 주식들을 날짜별 딕셔너리로 변환합니다.
        
        Args:
            selected_stocks (pd.DataFrame): 선택된 주식 데이터
            
        Returns:
            Dict[datetime, List[str]]: 날짜별 주식 목록
        """
        return {
            date: selected_stocks[
                selected_stocks['investment_date'] == date
            ]['StockID'].tolist()
            for date in selected_stocks['investment_date'].unique()
        }

    def get_valid_dates(self, 
                       date: datetime, 
                       date_index: pd.DatetimeIndex) -> Tuple[datetime, datetime]:
        """
        주어진 날짜에 대한 유효한 시작일과 종료일을 반환합니다.
        
        Args:
            date (datetime): 기준 날짜
            date_index (pd.DatetimeIndex): 유효한 거래일 인덱스
            
        Returns:
            Tuple[datetime, datetime]: 유효한 시작일과 종료일
        """
        future_dates = date_index[date_index > pd.Timestamp(date)]
        past_dates = date_index[date_index < pd.Timestamp(date)]
        
        next_date = future_dates.min() if not future_dates.empty else None
        prev_date = past_dates.max() if not past_dates.empty else None
        
        return next_date, prev_date

    def process_portfolio_stocks(self, 
                               ensemble_results: pd.DataFrame,
                               n_stocks_100: int = 100) -> Dict[str, Dict[datetime, List[str]]]:
        """
        포트폴리오 구성을 위한 주식들을 처리합니다.
        
        Args:
            ensemble_results (pd.DataFrame): 앙상블 결과 데이터
            n_stocks_100 (int): 기본 선택 주식 수
            
        Returns:
            Dict[str, Dict[datetime, List[str]]]: 포트폴리오별 날짜별 주식 목록
        """
        n_stocks_10 = round(len(ensemble_results['StockID'].unique()) / 10)
        
        selected_stocks_top_100 = self.select_stocks(ensemble_results, n_stocks_100, True)
        selected_stocks_bottom_100 = self.select_stocks(ensemble_results, n_stocks_100, False)
        selected_stocks_top_10 = self.select_stocks(ensemble_results, n_stocks_10, True)
        
        self.logger.info(f"Total unique stocks: {len(ensemble_results['StockID'].unique())}")
        self.logger.info(f"Top 100 stocks: {len(selected_stocks_top_100['StockID'].unique())}")
        self.logger.info(f"Bottom 100 stocks: {len(selected_stocks_bottom_100['StockID'].unique())}")
        self.logger.info(f"Top {n_stocks_10} stocks: {len(selected_stocks_top_10['StockID'].unique())}")
        
        return {
            'Top 100': self.create_stock_dict(selected_stocks_top_100),
            f'Top {n_stocks_10}': self.create_stock_dict(selected_stocks_top_10),
            'Bottom 100': self.create_stock_dict(selected_stocks_bottom_100)
        }

    def calculate_portfolio_returns(self,
                                  us_ret: pd.DataFrame,
                                  selected_stocks: Dict[datetime, List[str]],
                                  valid_stock_ids: Set[str]) -> Tuple[pd.Series, List[datetime]]:
        """
        선택된 주식들의 포트폴리오 수익률을 계산합니다.
        
        Args:
            us_ret (pd.DataFrame): 주식 수익률 데이터
            selected_stocks (Dict[datetime, List[str]]): 선택된 주식 목록
            valid_stock_ids (Set[str]): 유효한 주식 ID 목록
            
        Returns:
            Tuple[pd.Series, List[datetime]]: 누적 수익률과 리밸런싱 날짜 목록
        """
        portfolio_returns = pd.Series(dtype=float)
        rebalance_dates = []
        
        dates = sorted(selected_stocks.keys())
        for i, investment_date in enumerate(dates):
            stock_ids = selected_stocks[investment_date]
            valid_stock_ids_for_period = list(set(stock_ids) & valid_stock_ids)
            
            start_date, _ = self.get_valid_dates(investment_date, us_ret.index)
            if start_date is None:
                continue
                
            rebalance_dates.append(start_date)
            
            if i < len(dates) - 1:
                _, end_date = self.get_valid_dates(dates[i+1], us_ret.index)
                if end_date is None or end_date <= start_date:
                    continue
            else:
                end_date = us_ret.index[-1]
            
            period_returns = us_ret.loc[start_date:end_date, valid_stock_ids_for_period]
            if period_returns.empty:
                continue
            
            weights = np.array([1 / len(valid_stock_ids_for_period)] * len(valid_stock_ids_for_period))
            daily_portfolio_returns = period_returns.dot(weights)
            
            portfolio_returns = portfolio_returns.add(daily_portfolio_returns, fill_value=0)
        
        portfolio_returns = portfolio_returns.sort_index()
        cumulative_returns = (1 + portfolio_returns).cumprod()
        
        return cumulative_returns, rebalance_dates

    def process_ensemble_results(self, 
                               base_folder: str, 
                               model: str, 
                               window_size: int) -> pd.DataFrame:
        """
        앙상블 결과를 처리하고 저장합니다.
        
        Args:
            base_folder (str): 기본 폴더 경로
            model (str): 모델 이름
            window_size (int): 윈도우 크기
            
        Returns:
            pd.DataFrame: 처리된 앙상블 결과
        """
        work_folder = os.path.join(base_folder, 'WORK_DIR')
        
        folder_path = os.path.join(
            work_folder, 
            f'{model}{window_size}', 
            f'{window_size}D20P', 
            'ensem_res'
        )
        output_path = os.path.join(
            work_folder, 
            f'ensemble_{model}{window_size}_res.csv'
        )
        
        self.logger.info(f'Processing Ensemble {model}{window_size}...')
        
        try:
            # 앙상블 결과 폴더에서 모든 CSV 파일 처리
            all_data = []
            for file in os.listdir(folder_path):
                if file.endswith('.csv') and 'ensem' in file:
                    file_path = os.path.join(folder_path, file)
                    df = pd.read_csv(file_path)
                    
                    df['ending_date'] = pd.to_datetime(df['ending_date'])
                    df['investment_date'] = df['ending_date'] - pd.DateOffset(months=1)
                    df['StockID'] = df['StockID'].astype(str)
                    
                    all_data.append(df)
            
            if not all_data:
                raise ValueError(f"No CSV files found in {folder_path}")
            
            # 데이터 병합 및 정리
            combined_df = pd.concat(all_data)
            combined_df.sort_values(['investment_date', 'StockID'], inplace=True)
            
            # 불필요한 컬럼 제거
            combined_df = combined_df.loc[:, ~combined_df.columns.str.contains('^Unnamed')]
            if 'MarketCap' in combined_df.columns:
                combined_df.drop('MarketCap', axis=1, inplace=True)
            
            # 결과 저장
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            combined_df.reset_index(drop=True).to_csv(output_path)
            
            self.logger.info(f'Ensemble results saved to {output_path}')
            self.logger.info(f'Shape of the dataframe: {combined_df.shape}')
            
            return combined_df
            
        except Exception as e:
            self.logger.error(f'Error processing {model}{window_size}: {str(e)}')
            return pd.DataFrame()
    
    