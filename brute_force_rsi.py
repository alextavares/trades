import pandas as pd
import numpy as np
import requests

def get_data(limit=2000):
    url = f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=5m&limit={limit}"
    data = requests.get(url).json()
    df = pd.DataFrame(data, columns=['ts', 'o', 'h', 'l', 'c', 'v', 'ct', 'qa', 'nt', 'tb', 'tq', 'i'])
    df[['o', 'h', 'l', 'c']] = df[['o', 'h', 'l', 'c']].astype(float)
    df['cor'] = np.where(df['c'] > df['o'], 1, np.where(df['c'] < df['o'], -1, 0))
    return df

def calculate_indicators(df):
    df['sma'] = df['c'].rolling(20).mean()
    df['std'] = df['c'].rolling(20).std()
    df['upper'] = df['sma'] + (df['std'] * 2)
    df['lower'] = df['sma'] - (df['std'] * 2)
    
    delta = df['c'].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=13, adjust=False).mean()
    avg_loss = loss.ewm(com=13, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    df['rsi'] = 100 - (100 / (1 + rs))
    return df

def test_buy_strategy(df, rsi_val):
    trades = []
    for i in range(1, len(df) - 1):
        prev = df.iloc[i-1]
        curr = df.iloc[i]
        
        # Estratégia de COMPRA: Tocou banda inferior + RSI < rsi_val + Vermelho -> Verde
        if prev['l'] <= prev['lower'] and prev['rsi'] < rsi_val and prev['cor'] == -1 and curr['cor'] == 1:
            entrada = df['c'].iloc[i]
            saida = df['c'].iloc[i+1]
            trades.append(saida > entrada) # Ganha se subir
            
    if not trades: return 0, 0
    win_rate = sum(trades) / len(trades) * 100
    return win_rate, len(trades)

def main():
    df = get_data(2000)
    df = calculate_indicators(df)
    
    print(f"--- OTIMIZAÇÃO DE RSI PARA COMPRA (BTC 5m) ---")
    print(f"{'RSI <':<10} | {'Win Rate':<10} | {'Trades':<10}")
    print("-" * 35)
    
    for rsi in range(15, 45, 2):
        wr, count = test_buy_strategy(df, rsi)
        if count > 2: 
            print(f"{rsi:<10} | {wr:>8.2f}% | {count:<10}")
    
    print("-" * 35)

if __name__ == "__main__":
    main()
