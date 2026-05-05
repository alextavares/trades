import requests
import json

def debug_polymarket():
    print("--- DIAGNÓSTICO POLYMARKET ---")
    
    # 1. Testar Busca
    url_gamma = "https://gamma-api.polymarket.com/markets?active=true&limit=20&query=BTC%205m"
    print(f"Buscando em: {url_gamma}")
    res = requests.get(url_gamma).json()
    
    if not res:
        print("Erro: Busca voltou vazia!")
        return

    btc_market = None
    for m in res:
        print(f"Encontrado: {m.get('question')}")
        if "BTC" in m.get('question', '').upper() and "5M" in m.get('question', '').upper():
            btc_market = m
            break
            
    if not btc_market:
        print("Erro: Mercado BTC 5m não encontrado na lista acima.")
        return
        
    print(f"\nMercado Selecionado: {btc_market['question']}")
    token_ids = btc_market.get('clobTokenIds')
    print(f"Token IDs: {token_ids}")
    
    if not token_ids:
        print("Erro: Mercado não tem clobTokenIds!")
        return
        
    if isinstance(token_ids, str):
        token_ids = json.loads(token_ids)
        
    # 2. Testar Preço (CLOB)
    token_yes = token_ids[0]
    url_clob = f"https://clob.polymarket.com/price?token_id={token_yes}&side=BUY"
    print(f"Buscando preço em: {url_clob}")
    res_clob = requests.get(url_clob)
    print(f"Resposta CLOB: {res_clob.status_code} - {res_clob.text}")

if __name__ == "__main__":
    debug_polymarket()
