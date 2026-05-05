import time
import datetime
import os
import requests
import pandas as pd
from dotenv import load_dotenv

# Carregar ambiente para caso queira usar as chaves de API para ler as odds
load_dotenv()

# --- CONFIGURAÇÕES ---
SYMBOL = "BTCUSDT"
LOG_FILE = "trading_observer_log.csv"

def get_binance_price():
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={SYMBOL}"
    return float(requests.get(url, timeout=10).json()['price'])

def get_polymarket_odds():
    """Busca as odds reais no Polymarket para o mercado 'Live' de BTC 5m."""
    try:
        # 1. Buscar eventos da série BTC 5m (URL oficial capturada do frontend)
        url_series = "https://gamma-api.polymarket.com/events?series_slug=btc-up-or-down-5m&active=true&closed=false"
        events = requests.get(url_series, timeout=10).json()
        
        btc_market = None
        agora_ts = time.time()
        
        # Percorrer a lista de eventos retornados
        for event in events:
            markets = event.get('markets', [])
            for m in markets:
                start_ts = pd.to_datetime(m['startDate']).timestamp()
                end_ts = pd.to_datetime(m['endDate']).timestamp()
                
                # Procura o mercado onde "agora" está dentro da janela
                if start_ts <= agora_ts <= end_ts:
                    btc_market = m
                    break
            if btc_market: break
        
        if not btc_market:
            return None, None, "Nenhum mercado live encontrado na série (Filtro: active=true, closed=false)"
        
        # 2. Pegar as odds via CLOB
        token_ids = btc_market['clobTokenIds']
        if isinstance(token_ids, str):
            import json
            token_ids = json.loads(token_ids)
            
        token_yes = token_ids[0]
        token_no = token_ids[1]
        
        url_clob = "https://clob.polymarket.com/price?token_id="
        res_yes = requests.get(url_clob + token_yes + "&side=BUY", timeout=10).json()
        res_no = requests.get(url_clob + token_no + "&side=BUY", timeout=10).json()
        
        p_yes = float(res_yes.get('price', 0))
        p_no = float(res_no.get('price', 0))
        
        return p_yes, p_no, btc_market['question']
    except Exception as e:
        print(f"Erro ao buscar odds live: {e}")
        return None, None, None

def log_event(data):
    """Salva os dados no arquivo CSV."""
    df = pd.DataFrame([data])
    if not os.path.isfile(LOG_FILE):
        df.to_csv(LOG_FILE, index=False)
    else:
        df.to_csv(LOG_FILE, mode='a', header=False, index=False)

def main():
    print("=== INICIANDO ROBÔ OBSERVADOR (PAPER TRADING) ===")
    print(f"Monitorando {SYMBOL} e Polymarket Odds...")
    print(f"Log será salvo em: {LOG_FILE}")
    
    last_window_start = None
    target_price = None
    anchor_candle = None
    
    while True:
        try:
            agora = datetime.datetime.now()
            
            # Heartbeat: Print a cada minuto (aos 30s) para saber que está vivo
            if agora.second == 30:
                print(f"[{agora.strftime('%H:%M:%S')}] Coração batendo... Aguardando janela.")

            # Início da janela (múltiplo de 5)
            window_start = agora.replace(second=0, microsecond=0)
            while window_start.minute % 5 != 0:
                window_start -= datetime.timedelta(minutes=1)
            
            # Se mudou de janela, resetamos os dados
            if window_start != last_window_start:
                print(f"\n--- Nova Janela Detectada: {window_start.strftime('%H:%M')} ---")
                last_window_start = window_start
                target_price = get_binance_price()
                print(f"Preço Alvo (Target): ${target_price}")
                anchor_candle = None
                trade_logged = False

            segundos_passados = (agora - window_start).total_seconds()
            
            # Heartbeat a cada 60s para mostrar que o robô está vivo
            if int(segundos_passados) % 60 == 0 and int(segundos_passados) > 0:
                print(f"[{agora.strftime('%H:%M:%S')}] Monitorando Janela {window_start.strftime('%H:%M')}... (Coração batendo)")

            # 1. Final do Minuto 1: Capturar o Candle Âncora (Janela de 15s para não perder)
            if 60 <= segundos_passados < 75 and anchor_candle is None:
                print(f"[{agora.strftime('%H:%M:%S')}] Calculando Candle Âncora...")
                current_price = get_binance_price()
                anchor_dir = "UP" if current_price > target_price else "DOWN"
                anchor_change = abs(current_price - target_price) / target_price * 100
                anchor_candle = {'dir': anchor_dir, 'change': anchor_change, 'price': current_price}
                print(f"✅ Minuto 1 concluído. Âncora: {anchor_dir} ({anchor_change:.3f}%)")

            # 2. Minuto 1:30 (90 segundos): Checar Continuação e Odds (Janela de 3 min para persistência)
            if 90 <= segundos_passados < 270 and anchor_candle and not trade_logged:
                print(f"[{agora.strftime('%H:%M:%S')}] Tentando capturar odds...")
                current_price = get_binance_price()
                current_dir = "UP" if current_price > anchor_candle['price'] else "DOWN"
                
                odd_yes, odd_no, market_name = get_polymarket_odds()
                
                if odd_yes is not None and odd_no is not None:
                    sinal = anchor_candle['dir'] if anchor_candle['dir'] == current_dir else "CONFLITO"
                    log_data = {
                        'timestamp': agora.strftime('%Y-%m-%d %H:%M:%S'),
                        'window': window_start.strftime('%H:%M'),
                        'target_price': target_price,
                        'price_1m30': current_price,
                        'odd_yes': odd_yes,
                        'odd_no': odd_no,
                        'anchor_dir': anchor_candle['dir'],
                        'current_dir': current_dir,
                        'signal': sinal,
                        'market': market_name
                    }
                    log_event(log_data)
                    trade_logged = True
                    print(f"💰 SUCESSO: Dados salvos. Sinal: {sinal} | Odds: Y:{odd_yes} N:{odd_no}")
                else:
                    print(f"⚠️ Aviso: Falha ao obter odds ({market_name}). Tentando novamente em 1s...")

        except Exception as e:
            print(f"Erro no loop do observador: {e}")
            
        time.sleep(1)

if __name__ == "__main__":
    main()
