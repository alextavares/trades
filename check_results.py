import pandas as pd
import requests
import time
from datetime import datetime, timedelta

def get_historical_price(timestamp_str):
    # Forçamos o uso da data REAL (2024) para a Binance entender
    # O timestamp_str vem como "2026-04-29 HH:MM:SS"
    _, time_part = timestamp_str.split(' ')
    real_date = "2024-04-30" # Data real de hoje no mundo lá fora
    
    local_dt = datetime.strptime(f"{real_date} {time_part}", "%Y-%m-%d %H:%M:%S")
    utc_dt = local_dt + timedelta(hours=3) # Ajuste para UTC
    ts_ms = int(utc_dt.timestamp() * 1000)
    
    url = f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&startTime={ts_ms}&limit=1"
    res = requests.get(url).json()
    if res and len(res) > 0:
        return float(res[0][4]) # Close price
    return None

df = pd.read_csv('trading_observer_log.csv')
print(f"Lendo {len(df)} registros do CSV...")
df = df.drop_duplicates(subset=['window'])
print(f"Analisando {len(df)} janelas únicas...")

results = []
for index, row in df.iterrows():
    window_time = row['window']
    row_date = row['timestamp'].split(' ')[0]
    h, m = map(int, window_time.split(':'))
    
    # Início e Fim da janela
    start_time = datetime.strptime(f"{row_date} {h}:{m}:00", "%Y-%m-%d %H:%M:%S")
    end_time = start_time + timedelta(minutes=5)
    
    # Preços Reais
    p_start = get_historical_price(start_time.strftime('%Y-%m-%d %H:%M:%S'))
    p_end = get_historical_price(end_time.strftime('%Y-%m-%d %H:%M:%S'))
    
    if p_start and p_end:
        signal = row['signal']
        outcome = "UP" if p_end > p_start else "DOWN"
        win = (signal == outcome)
        
        results.append({
            'Janela': window_time,
            'Sinal': signal,
            'Movimento Real': f"{p_start:,.0f} -> {p_end:,.0f} ({outcome})",
            'Resultado': "WIN ✅" if win else "LOSS ❌"
        })

if results:
    print("\n--- PERFORMANCE REAL DOS SINAIS ---")
    print(pd.DataFrame(results).to_markdown(index=False))
    win_count = sum(1 for r in results if "WIN" in r['Resultado'])
    print(f"\nWin Rate: {(win_count/len(results))*100:.1f}% ({win_count}/{len(results)})")
else:
    print("\nNenhum resultado processado.")
