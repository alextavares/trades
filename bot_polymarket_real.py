import time
import datetime
import os
import json
import requests
import pandas as pd
import numpy as np
from dotenv import load_dotenv

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType, ApiCreds
    from py_clob_client.order_builder.constants import BUY
    CLOB_AVAILABLE = True
except ImportError:
    CLOB_AVAILABLE = False
    print("⚠️  Aviso: 'py-clob-client' não instalado. Bot em modo SIMULAÇÃO.")

load_dotenv()

# --- CONFIGURAÇÕES ---
SYMBOL             = "BTCUSDT"
INTERVAL           = "5m"
RSI_THRESHOLD_SELL = 70   # RSI acima deste valor = sobrecomprado
TRADE_AMOUNT_USDC  = 1.0  # Valor por operação em USDC
LOG_FILE           = "bot_real.log"

def log_message(msg):
    ts   = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def get_binance_data():
    """Puxa candles de 5m da Binance para calcular RSI e Bollinger."""
    url = f"https://api.binance.com/api/v3/klines?symbol={SYMBOL}&interval={INTERVAL}&limit=100"
    res = requests.get(url, timeout=10).json()
    df  = pd.DataFrame(res, columns=['ts','o','h','l','c','v','ct','qa','nt','tb','tq','i'])
    df[['o','h','l','c']] = df[['o','h','l','c']].astype(float)
    df['cor'] = np.where(df['c'] > df['o'], 1, np.where(df['c'] < df['o'], -1, 0))
    return df

def calculate_indicators(df):
    """Calcula Bandas de Bollinger e RSI."""
    df['sma']   = df['c'].rolling(20).mean()
    df['std']   = df['c'].rolling(20).std()
    df['upper'] = df['sma'] + (df['std'] * 2)

    delta    = df['c'].diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=13, adjust=False).mean()
    avg_loss = loss.ewm(com=13, adjust=False).mean()
    rs       = avg_gain / (avg_loss + 1e-9)
    df['rsi'] = 100 - (100 / (1 + rs))
    return df

def find_active_market():
    """Busca o mercado BTC Up/Down 5m que está ATIVO AGORA."""
    try:
        url    = "https://gamma-api.polymarket.com/events?series_slug=btc-up-or-down-5m&active=true&closed=false"
        events = requests.get(url, timeout=10).json()
        now_ts = time.time()

        for event in events:
            for m in event.get('markets', []):
                start_ts = pd.to_datetime(m['startDate']).timestamp()
                end_ts   = pd.to_datetime(m['endDate']).timestamp()
                if start_ts <= now_ts <= end_ts:
                    return m
        return None
    except Exception as e:
        log_message(f"Erro ao buscar mercado: {e}")
        return None

def execute_trade_reversal():
    """
    Estratégia: REVERSÃO (RSI + Bollinger)
    Quando RSI > 70 e preço tocou banda superior e houve reversão de candle,
    apostamos que o preço VAI CAIR → compramos token NO (índice 1).
    """
    pk     = os.getenv("PK", "")
    funder = os.getenv("FUNDER", "")

    if not pk or pk.startswith("0x000"):
        log_message("💡 SIMULAÇÃO: Sinal de reversão detectado (sem PK no .env).")
        return

    if not CLOB_AVAILABLE:
        log_message("❌ ERRO: py-clob-client não instalado. Ordem não enviada.")
        return

    log_message("🚨 SINAL REAL [REVERSÃO DOWN]! Iniciando execução de ordem...")

    # Buscar mercado com até 30s de persistência (API pode ter delay)
    market = None
    for tentativa in range(15):
        market = find_active_market()
        if market:
            break
        log_message(f"  Tentativa {tentativa+1}/15: mercado não disponível ainda, aguardando 2s...")
        time.sleep(2)

    if not market:
        log_message("❌ FALHA: Mercado não encontrado após 30s. Ordem cancelada.")
        return

    log_message(f"✅ Mercado encontrado: {market['question']}")

    # Extrair token NO (índice 1 = apostamos que preço cai = DOWN = NO)
    try:
        token_ids = market.get('clobTokenIds', '[]')
        if isinstance(token_ids, str):
            token_ids = json.loads(token_ids)
        token_id = token_ids[1]  # índice 1 = NO / DOWN
        log_message(f"  Token NO (DOWN) selecionado | ID: {token_id[:20]}...")
    except Exception as e:
        log_message(f"❌ ERRO ao extrair token_id: {e}")
        return

    # Verificar preço atual do token para calcular o tamanho correto em shares
    preco = None
    try:
        res   = requests.get(f"https://clob.polymarket.com/price?token_id={token_id}&side=BUY", timeout=10).json()
        preco = float(res.get('price', 0))
        log_message(f"  Preço atual do token NO: {preco:.3f} ({preco*100:.1f}% implícito)")
        if preco <= 0:
            log_message("❌ Preço inválido retornado pela API. Cancelando.")
            return
        if preco > 0.90:
            log_message(f"⚠️  Preço muito alto ({preco:.3f}). Risco/Retorno ruim. Cancelando.")
            return
    except Exception as e:
        log_message(f"⚠️  Não foi possível verificar preço: {e}. Cancelando por segurança.")
        return

    # size = shares a comprar = valor_em_usd / preco_do_token  (API pUSD usa shares, não USD)
    shares = round(TRADE_AMOUNT_USDC / preco, 2)
    log_message(f"  Calculando shares: ${TRADE_AMOUNT_USDC} / {preco:.3f} = {shares} shares")

    # Enviar ordem via CLOB (padrão novo: create_order + post_order)
    try:
        creds = None
        if os.getenv("API_KEY"):
            creds = ApiCreds(
                api_key=os.getenv("API_KEY"),
                api_secret=os.getenv("API_SECRET"),
                api_passphrase=os.getenv("API_PASSPHRASE")
            )

        client = ClobClient(
            host="https://clob.polymarket.com",
            chain_id=137,
            key=pk,
            creds=creds,
            funder=funder,
            signature_type=1
        )

        if not creds:
            log_message("  Gerando credenciais pela primeira vez...")
            new_creds = client.create_or_derive_api_creds()
            log_message(f"  API_KEY={new_creds.api_key}")
            log_message(f"  API_SECRET={new_creds.api_secret}")
            log_message(f"  API_PASSPHRASE={new_creds.api_passphrase}")
            log_message("  ⚠️  SALVE ESSAS CREDENCIAIS NO SEU .ENV!")
            client.set_api_creds(new_creds)

        # Novo padrão pUSD: create_order → post_order com OrderType.GTC
        signed_order = client.create_order(
            OrderArgs(
                token_id=token_id,
                price=round(preco + 0.01, 2),  # Bid ligeiramente acima do mid → garante fill
                size=shares,
                side=BUY
            )
        )
        resp = client.post_order(signed_order, OrderType.GTC)
        log_message(f"✅ ORDEM ENVIADA COM SUCESSO! Resposta: {resp}")

    except Exception as e:
        log_message(f"❌ ERRO CRÍTICO ao enviar ordem: {type(e).__name__}: {e}")

def main():
    print("=== BOT POLYMARKET BTC 5M - ESTRATÉGIA: REVERSÃO ===")
    print(f"  Sinal: RSI > {RSI_THRESHOLD_SELL} + Tocou Banda Superior + Candle Reverteu")
    print(f"  Ação:  Comprar NO (DOWN) — aposta que o preço vai cair")
    print(f"  Valor por operação: ${TRADE_AMOUNT_USDC} USDC\n")

    pk = os.getenv("PK", "")
    print("--- Verificando conexão inicial ---")
    if pk and not pk.startswith("0x000"):
        try:
            creds = None
            if os.getenv("API_KEY"):
                creds = ApiCreds(
                    api_key=os.getenv("API_KEY"),
                    api_secret=os.getenv("API_SECRET"),
                    api_passphrase=os.getenv("API_PASSPHRASE")
                )
            client = ClobClient(
                host="https://clob.polymarket.com",
                chain_id=137,
                key=pk,
                creds=creds,
                funder=os.getenv("FUNDER", "")
            )
            if not creds:
                print("Gerando/recuperando credenciais da API...")
                new_creds = client.create_or_derive_api_creds()
                print(f"API_KEY={new_creds.api_key}")
                print(f"API_SECRET={new_creds.api_secret}")
                print(f"API_PASSPHRASE={new_creds.api_passphrase}")
                print("⚠️  SALVE ESSAS CREDENCIAIS NO SEU .ENV!")
            else:
                print("Conexão com Polymarket OK (Credenciais carregadas).")
        except Exception as e:
            print(f"Erro na conexão inicial: {e}")
    else:
        print("⚠️  PK não configurado. Rodando em modo SIMULAÇÃO.")

    log_message("Aguardando próximo ciclo de 5 minutos...")

    while True:
        try:
            agora = datetime.datetime.now()

            # Disparar aos 5s de cada múltiplo de 5 minutos
            if agora.minute % 5 == 0 and agora.second == 5:
                log_message(f"--- Verificando ciclo {agora.strftime('%H:%M')} ---")

                df  = get_binance_data()
                df  = calculate_indicators(df)

                ultimo    = df.iloc[-2]  # Candle que acabou de fechar
                penultimo = df.iloc[-3]  # Candle anterior

                # Condições da estratégia de reversão
                is_overextended = (penultimo['h'] >= penultimo['upper'] and
                                   penultimo['rsi'] > RSI_THRESHOLD_SELL)
                is_reversal     = (penultimo['cor'] == 1 and ultimo['cor'] == -1)

                log_message(
                    f"BTC: ${ultimo['c']:,.2f} | "
                    f"RSI: {penultimo['rsi']:.2f} | "
                    f"Banda: {is_overextended} | "
                    f"Reversão: {is_reversal}"
                )

                if is_overextended and is_reversal:
                    execute_trade_reversal()

                time.sleep(60)  # Dorme 1 min para não repetir no mesmo ciclo

        except Exception as e:
            log_message(f"Erro no loop principal: {type(e).__name__}: {e}")

        time.sleep(0.5)

if __name__ == "__main__":
    main()
