import time
import datetime
import pandas as pd
import numpy as np
import requests

def get_binance_data():
    """Baixa as últimas 100 velas de 5 minutos da Binance."""
    url = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=5m&limit=100"
    response = requests.get(url)
    data = response.json()
    df = pd.DataFrame(data, columns=['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume', 'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'])
    
    # Converter para numérico
    df['Open'] = df['Open'].astype(float)
    df['High'] = df['High'].astype(float)
    df['Low'] = df['Low'].astype(float)
    df['Close'] = df['Close'].astype(float)
    
    # Identificar cor (1 = Verde, -1 = Vermelho, 0 = Doji)
    df['Cor'] = np.where(df['Close'] > df['Open'], 1, np.where(df['Close'] < df['Open'], -1, 0))
    return df

def process_logic(df):
    """Calcula os indicadores e verifica a regra de venda."""
    # 1. Bandas de Bollinger (20, 2)
    df['SMA_20'] = df['Close'].rolling(window=20).mean()
    df['STD_20'] = df['Close'].rolling(window=20).std()
    df['Upper_Band'] = df['SMA_20'] + (df['STD_20'] * 2)
    
    # 2. RSI (14)
    delta = df['Close'].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=13, adjust=False).mean()
    avg_loss = loss.ewm(com=13, adjust=False).mean()
    rs = avg_gain / avg_loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    # 3. Identificar os candles corretos
    # Como rodamos o bot nos primeiros segundos após o fechamento do candle de 5m,
    # a última linha (iloc[-1]) será o NOVO candle que acabou de abrir.
    # O candle que nos interessa é o que ACABOU de fechar (iloc[-2]) e o anterior a ele (iloc[-3])
    
    ultimo = df.iloc[-2]     # O candle que acabou de fechar
    penultimo = df.iloc[-3]  # O candle que antecede o último
    
    print(f"Status do Mercado:")
    print(f" -> Penúltimo Candle: Cor {penultimo['Cor']}, RSI: {penultimo['RSI']:.2f}, Máxima: {penultimo['High']:.2f}, Banda Sup: {penultimo['Upper_Band']:.2f}")
    print(f" -> Último Candle: Cor {ultimo['Cor']}")
    
    # LÓGICA DE VENDA (nossa estratégia vencedora)
    # 1. Penultimo era verde (1)
    # 2. Ultimo é vermelho (-1)
    # 3. Penultimo tocou/rompeu a banda superior
    # 4. Penultimo tinha RSI de euforia (> 70)
    if penultimo['Cor'] == 1 and ultimo['Cor'] == -1:
        if penultimo['High'] >= penultimo['Upper_Band'] and penultimo['RSI'] > 70:
            return "VENDA"
            
    return "AGUARDAR"

def execute_polymarket_trade(direcao):
    """Função Placeholder que conectará com a carteira real do Polymarket."""
    print("=========================================================")
    print(f"🚀 ALERTA DE SINAL! ENVIANDO ORDEM PARA O POLYMARKET...")
    print(f"🎯 Apostando em: {direcao} (Bitcoin vai cair nos próximos 5 min)")
    print("=========================================================\n")
    # AQUI DEPOIS VAMOS COLOCAR O CÓDIGO DA API DO POLYMARKET:
    # 1. Obter o mercado ativo atual de BTC 5m
    # 2. client.create_and_post_order(...)
    print("[Simulação] Ordem executada com sucesso!\n")

def run_bot():
    print("Iniciando Bot Polymarket (Tempo Real)...")
    print("Estratégia: Apenas Venda (Bollinger + RSI > 70)")
    print("O robô fará a verificação a cada 5 minutos no relógio (ex: 10:05, 10:10...).")
    print("Aguardando...\n")
    
    while True:
        agora = datetime.datetime.now()
        
        # Queremos rodar a lógica a cada 5 minutos, exatos 3 segundos após virar o minuto 
        # (Para dar tempo da Binance atualizar a vela fechada no sistema deles)
        if agora.minute % 5 == 0 and agora.second == 3:
            print(f"[{agora.strftime('%H:%M:%S')}] Fechamento de candle de 5m detectado! Processando dados...")
            
            try:
                df = get_binance_data()
                sinal = process_logic(df)
                
                if sinal == "VENDA":
                    # No Polymarket, para apostar que o BTC cai, compramos ações de "NO" (Não)
                    execute_polymarket_trade("NO")
                else:
                    print("Nenhum sinal gerado. Aguardando o próximo candle de 5 minutos...\n")
                    
            except Exception as e:
                print(f"Erro ao conectar ao mercado: {e}")
                
            # Dorme 60 segundos para evitar disparar a lógica várias vezes no mesmo minuto
            time.sleep(60)
        else:
            # Checa o relógio a cada meio segundo
            time.sleep(0.5)

if __name__ == "__main__":
    run_bot()
