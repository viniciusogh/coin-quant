"""
데이터 수집 모듈 — REST API로 OHLCV 과거 데이터 + 현재가 조회
"""

import os
import logging
import time
import pandas as pd
from binance.client import Client
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class DataFetcher:
    def __init__(self, symbol: str = None):
        self.symbol = symbol or os.getenv("SYMBOL", "BTCUSDT")
        self.client = Client(
            api_key=os.getenv("BINANCE_API_KEY"),
            api_secret=os.getenv("BINANCE_SECRET_KEY"),
        )
        logger.info(f"DataFetcher 초기화 — 심볼: {self.symbol}")

    def get_ohlcv(self, interval: str = "1h", limit: int = 500) -> pd.DataFrame:
        """
        OHLCV 캔들 데이터 조회
        interval: '1m', '5m', '15m', '1h', '4h', '1d'
        limit: 최대 1500
        """
        logger.info(f"OHLCV 요청: {self.symbol} {interval} x{limit}")
        try:
            raw = self.client.futures_klines(
                symbol=self.symbol,
                interval=interval,
                limit=limit,
            )
            cols = [
                "open_time", "open", "high", "low", "close", "volume",
                "close_time", "quote_volume", "trades",
                "taker_buy_base", "taker_buy_quote", "ignore",
            ]
            df = pd.DataFrame(raw, columns=cols)
            # 타입 변환 (Copy-on-Write 호환)
            df = df.assign(
                open_time=pd.to_datetime(df["open_time"], unit="ms"),
                close_time=pd.to_datetime(df["close_time"], unit="ms"),
                open=df["open"].astype(float),
                high=df["high"].astype(float),
                low=df["low"].astype(float),
                close=df["close"].astype(float),
                volume=df["volume"].astype(float),
            )
            df = df[["open_time", "open", "high", "low", "close", "volume"]].copy()
            df.set_index("open_time", inplace=True)
            logger.info(f"OHLCV 수신 완료: {len(df)}개 캔들 ({df.index[0]} ~ {df.index[-1]})")
            return df
        except Exception as e:
            logger.error(f"OHLCV 조회 실패: {e}")
            raise

    def get_price(self) -> float:
        """BTC 현재가 조회"""
        ticker = self.client.futures_symbol_ticker(symbol=self.symbol)
        price = float(ticker["price"])
        logger.info(f"현재가: {self.symbol} = ${price:,.2f}")
        return price

    def get_account_balance(self) -> dict:
        """USDT 잔고 조회"""
        balances = self.client.futures_account_balance()
        usdt = next((b for b in balances if b["asset"] == "USDT"), None)
        if not usdt:
            raise ValueError("USDT 잔고를 찾을 수 없습니다")
        result = {
            "balance": float(usdt["balance"]),
            "available": float(usdt["availableBalance"]),
        }
        logger.info(f"잔고: {result['balance']:.2f} USDT (가용: {result['available']:.2f})")
        return result

    def get_position(self) -> dict | None:
        """현재 포지션 조회 (없으면 None)"""
        positions = self.client.futures_position_information(symbol=self.symbol)
        pos = next((p for p in positions if p["symbol"] == self.symbol), None)
        if not pos or float(pos["positionAmt"]) == 0:
            return None
        return {
            "side": "LONG" if float(pos["positionAmt"]) > 0 else "SHORT",
            "amount": abs(float(pos["positionAmt"])),
            "entry_price": float(pos["entryPrice"]),
            "unrealized_pnl": float(pos["unRealizedProfit"]),
            "leverage": int(pos["leverage"]),
            "liquidation_price": float(pos["liquidationPrice"]),
        }


    def get_ohlcv_history(self, interval: str = "1h", days: int = 365) -> pd.DataFrame:
        """
        페이지네이션으로 장기 OHLCV 수집
        interval: '1h', '4h' 등
        days: 수집할 과거 일수
        """
        import time as time_module
        logger.info(f"장기 데이터 수집 시작: {self.symbol} {interval} {days}일치")

        end_ts = int(pd.Timestamp.now().timestamp() * 1000)
        start_ts = int((pd.Timestamp.now() - pd.Timedelta(days=days)).timestamp() * 1000)

        all_candles = []
        current_start = start_ts

        while current_start < end_ts:
            raw = self.client.futures_klines(
                symbol=self.symbol,
                interval=interval,
                startTime=current_start,
                limit=1500,
            )
            if not raw:
                break
            all_candles.extend(raw)
            current_start = raw[-1][0] + 1  # 마지막 캔들 다음 시간
            logger.info(f"  수집 중... {len(all_candles)}개")
            time_module.sleep(0.2)  # Rate limit 방지
            if len(raw) < 1500:
                break

        if not all_candles:
            raise ValueError("데이터 없음")

        df = pd.DataFrame(all_candles, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades",
            "taker_buy_base", "taker_buy_quote", "ignore",
        ])
        df = df.assign(
            open_time=pd.to_datetime(df["open_time"], unit="ms"),
            open=df["open"].astype(float),
            high=df["high"].astype(float),
            low=df["low"].astype(float),
            close=df["close"].astype(float),
            volume=df["volume"].astype(float),
        )
        df = df[["open_time", "open", "high", "low", "close", "volume"]].copy()
        df.set_index("open_time", inplace=True)
        df = df[~df.index.duplicated(keep="last")]
        logger.info(f"수집 완료: {len(df)}개 캔들 ({df.index[0]} ~ {df.index[-1]})")
        return df


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    fetcher = DataFetcher()

    # 연결 테스트
    price = fetcher.get_price()
    print(f"\nBTC 현재가: ${price:,.2f}")

    df = fetcher.get_ohlcv(interval="1h", limit=10)
    print(f"\n최근 10개 1h 캔들:")
    print(df.tail())

    balance = fetcher.get_account_balance()
    print(f"\n잔고: {balance['balance']:.2f} USDT")
