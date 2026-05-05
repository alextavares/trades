import pandas as pd
import numpy as np
import requests

def get_1m_data(total_limit=10000):
    all_data = []
    end_time = None
    
    print(f"Baixando as últimas {total_limit} velas de 1 minuto em blocos...")
    
    for _ in range(total_limit // 1000):
        url = f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=1000"
        if end_time:
            url += f"&endTime={end_time}"
        
        res = requests.get(url).json()
        if not res: break
        
        all_data = res + all_data
        end_time = res[0][0] - 1 # Pega o tempo da primeira vela do bloco e subtrai 1ms
        
    df = pd.DataFrame(all_data, columns=['ts', 'o', 'h', 'l', 'c', 'v', 'ct', 'qa', 'nt', 'tb', 'tq', 'i'])
    df[['o', 'h', 'l', 'c', 'v']] = df[['o', 'h', 'l', 'c', 'v']].astype(float)
    df['ts'] = pd.to_datetime(df['ts'], unit='ms')
    # Remover duplicatas e ordenar
    df = df.drop_duplicates(subset=['ts']).sort_values('ts').reset_index(drop=True)
    return df

import math
from scipy.stats import norm

def estimate_binary_odds(current_price, target_price, time_left_min, sigma=0.0005):
    """
    Simula a probabilidade (Odd) usando uma aproximação de Black-Scholes para opções binárias.
    sigma: volatilidade aproximada do BTC em 1min (0.05% = 0.0005)
    """
    if time_left_min <= 0: return 0.5
    t = time_left_min / 60 / 24 / 365 # Tempo em anos
    # Simplificação: d2 do Black-Scholes
    # Como o tempo é minúsculo (3 min), a deriva (drift) é quase zero.
    d2 = (math.log(current_price / target_price)) / (sigma * math.sqrt(time_left_min * 60))
    prob = norm.cdf(d2)
    return max(0.01, min(0.99, prob))

def run_continuation_backtest():
    df = get_1m_data(5000)
    df['vol_sma'] = df['v'].rolling(20).mean()
    
    total_ev = 0
    trades_count = 0
    wins = 0
    
    print("\n--- BACKTEST HONESTO: SIMULAÇÃO DE ODDS E EV ---")
    
    for i in range(25, len(df) - 5):
        if df['ts'].iloc[i].minute % 5 == 0:
            target_price = df['c'].iloc[i-1]
            anchor = df.iloc[i]
            second_min = df.iloc[i+1]
            
            # SIMULAÇÃO DE ENTRADA NO MINUTO 1.5 (Metade do segundo candle)
            # Preço de entrada estimado como a média do segundo candle
            entry_price = (second_min['o'] + second_min['c']) / 2
            
            anchor_dir = 1 if anchor['c'] > anchor['o'] else -1
            second_dir = 1 if second_min['c'] > second_min['o'] else -1
            
            # Filtros de sinal
            valid_momentum = (anchor_dir == second_dir) and (abs(anchor['c'] - anchor['o'])/anchor['o'] > 0.0005)
            
            if valid_momentum:
                # Estimamos a ODD no momento da entrada (faltando 3.5 minutos)
                odd_up = estimate_binary_odds(entry_price, target_price, 3.5)
                
                # Resultado final no minuto 5
                final_price = df['c'].iloc[i+4]
                
                if anchor_dir == 1: # Apostando no UP
                    is_win = final_price > target_price
                    price_paid = odd_up
                    # Se ganhar, recebe 1.0. Lucro = 1.0 - price_paid. Se perder, perde price_paid.
                    profit = (1.0 - price_paid) if is_win else -price_paid
                    
                    # Filtro de Odds do Claude: Entre 62c e 78c
                    if 0.60 <= price_paid <= 0.80:
                        total_ev += profit
                        trades_count += 1
                        if is_win: wins += 1
                
                elif anchor_dir == -1: # Apostando no DOWN
                    odd_down = 1.0 - odd_up
                    is_win = final_price < target_price
                    price_paid = odd_down
                    profit = (1.0 - price_paid) if is_win else -price_paid
                    
                    if 0.60 <= price_paid <= 0.80:
                        total_ev += profit
                        trades_count += 1
                        if is_win: wins += 1

    if trades_count > 0:
        print(f"Total de Trades: {trades_count}")
        print(f"Win Rate Real: {(wins/trades_count)*100:.2f}%")
        print(f"Lucro Líquido Acumulado: ${total_ev:.4f} por dólar apostado")
        print(f"EV Médio por Trade: ${(total_ev/trades_count):.4f}")
    else:
        print("Nenhum trade passou pelos filtros de Odds e Momentum.")

if __name__ == "__main__":
    run_continuation_backtest()
