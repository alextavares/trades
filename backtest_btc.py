import sys
import subprocess

def install(package):
    subprocess.check_call([sys.executable, "-m", "pip", "install", package, "--quiet"])

try:
    import pandas as pd
    import numpy as np
    import requests
except ImportError:
    print("Instalando dependências necessárias (pandas, requests)... aguarde.")
    install("pandas")
    install("requests")
    import pandas as pd
    import numpy as np
    import requests

def run_backtest():
    print("Baixando as últimas 2000 velas de 5 minutos reais do Bitcoin (BTC-USDT) via Binance...")
    try:
        url = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=5m&limit=2000"
        response = requests.get(url)
        data = response.json()
        df = pd.DataFrame(data, columns=['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume', 'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'])
        df['Open'] = df['Open'].astype(float)
        df['Close'] = df['Close'].astype(float)
        df['High'] = df['High'].astype(float)
        df['Low'] = df['Low'].astype(float)
    except Exception as e:
        print(f"Erro ao baixar dados: {e}")
        return
        
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)

    df = df.dropna()

    # 1. Definir a cor do candle (1 = Verde, -1 = Vermelho, 0 = Doji)
    df['Cor'] = np.where(df['Close'] > df['Open'], 1, np.where(df['Close'] < df['Open'], -1, 0))

    # 1.5 Calcular Bandas de Bollinger (20 períodos, 2 desvios padrão)
    df['SMA_20'] = df['Close'].rolling(window=20).mean()
    df['STD_20'] = df['Close'].rolling(window=20).std()
    df['Upper_Band'] = df['SMA_20'] + (df['STD_20'] * 2)
    df['Lower_Band'] = df['SMA_20'] - (df['STD_20'] * 2)
    
    # 1.6 Calcular RSI (14 periodos)
    delta = df['Close'].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=13, adjust=False).mean()
    avg_loss = loss.ewm(com=13, adjust=False).mean()
    rs = avg_gain / avg_loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    # --- LÓGICA: BOLLINGER + RSI + ENGOLFO ---
    df['Signal'] = 0
    
    for i in range(1, len(df)):
        prev = df.iloc[i-1]
        curr = df.iloc[i]
        
        # Condições para VENDA (DOWN):
        # 1. Candle anterior tocou/rompeu banda superior
        # 2. RSI estava acima de 70 (sobrecomprado)
        # 3. Candle atual é de ENGOLFO DE BAIXA (cobre o corpo do anterior)
        
        is_overextended = prev['High'] >= df['Upper_Band'].iloc[i-1] and df['RSI'].iloc[i-1] > 70
        is_red_candle = curr['Close'] < curr['Open']
        is_engulfing = (curr['Open'] >= prev['Close']) and (curr['Close'] < prev['Open'])
        
        if is_overextended and is_red_candle and is_engulfing:
            df.at[df.index[i], 'Signal'] = -1

    # --- SIMULAÇÃO DE RESULTADOS ---
    trades = []
    for i in range(len(df)):
        if df['Signal'].iloc[i] == -1:
            entrada = df['Close'].iloc[i]
            if i + 1 < len(df):
                saida = df['Close'].iloc[i+1]
                win = (saida < entrada) # Ganha se o próximo candle fechar abaixo (queda)
                trades.append(win)

    print(f"\n--- ESTRATÉGIA: BOLLINGER + RSI + ENGOLFO ---")
    if trades:
        win_rate = sum(trades) / len(trades) * 100
        print(f"Total de Sinais: {len(trades)}")
        print(f"Taxa de Acerto (Win Rate): {win_rate:.2f}%")
        print(f"---------------------------------------------")
    else:
        print("Nenhum sinal encontrado com este nível de rigor.")

if __name__ == '__main__':
    run_backtest()
