//+------------------------------------------------------------------+
//| EMA_Slope_Grid_Aggressive.mq4                                    |
//| EMA slope + contra-grid agressivo para conta demo/micro.          |
//| Sem stop loss. TP individual + fechamento por basket positivo.    |
//+------------------------------------------------------------------+
#property strict
#property version   "1.00"
#property description "EA agressivo: EMA slope define lado, reentradas contra o preco, TP individual e basket close."

//========================= Inputs: Sinal ============================
input int     InpEmaPeriod              = 20;      // Periodo da EMA
input int     InpSlopeLookbackBars      = 3;       // Quantos candles atras para medir slope
input int     InpAtrPeriod              = 14;      // ATR usado para normalizar slope
input double  InpMinSlopeAtr            = 0.15;    // Slope minimo em unidades de ATR

//========================= Inputs: Grid/TP ==========================
input double  InpLotSize                = 0.01;    // Lote fixo por ordem
input int     InpGridSpacingPips        = 15;      // Distancia contra para nova ordem
input int     InpTakeProfitPips         = 10;      // TP individual por ordem
input double  InpBasketTakeProfitMoney  = 2.00;    // Fecha o lado se lucro aberto >= valor
input int     InpMaxOrdersPerSide       = 100;     // Maximo de ordens por lado
input int     InpMagicNumber            = 20260501;// Magic number

//========================= Inputs: Execucao =========================
input int     InpMaxSpreadPips          = 3;       // Spread maximo permitido
input int     InpMaxSlippagePoints      = 30;      // Slippage em points
input bool    InpOneOrderPerBar         = true;    // Maximo 1 ordem por candle
input int     InpMinSecondsBetweenOrders= 10;      // Cooldown entre ordens
input bool    InpEnforceMinStopDistTP   = true;    // Ajusta TP ao StopLevel do broker

//========================= Globais =================================
double  g_point       = 0.0;
int     g_digits      = 0;
double  g_pipMult     = 1.0;
double  g_gridPoints  = 0.0;
double  g_tpPoints    = 0.0;

static datetime s_lastTickTime  = 0;
static datetime s_lastOrderTime = 0;
static datetime s_lastBarTime   = 0;

//========================= Helpers =================================
int LotDigitsFromStep(double step)
{
   if(step >= 1.0)   return 1;
   if(step >= 0.1)   return 1;
   if(step >= 0.01)  return 2;
   if(step >= 0.001) return 3;
   return 2;
}

double QuantizeLot(double lots, double step)
{
   if(step <= 0) return lots;
   return MathFloor(lots / step + 1e-9) * step;
}

double ValidLot(double requested)
{
   double minLot  = MarketInfo(Symbol(), MODE_MINLOT);
   double maxLot  = MarketInfo(Symbol(), MODE_MAXLOT);
   double stepLot = MarketInfo(Symbol(), MODE_LOTSTEP);

   if(minLot <= 0)  minLot = 0.01;
   if(maxLot <= 0)  maxLot = 100.0;
   if(stepLot <= 0) stepLot = 0.01;

   double lot = MathMax(minLot, MathMin(maxLot, requested));
   lot = QuantizeLot(lot, stepLot);
   if(lot < minLot) lot = minLot;
   return NormalizeDouble(lot, LotDigitsFromStep(stepLot));
}

double PipValue()
{
   return g_point * g_pipMult;
}

bool SpreadOk()
{
   RefreshRates();
   double spreadPips = (Ask - Bid) / PipValue();
   if(spreadPips <= InpMaxSpreadPips) return true;

   static datetime lastLog = 0;
   if(TimeCurrent() > lastLog + 30)
   {
      PrintFormat("Spread alto: %.1f pips > %d. Sem novas ordens.", spreadPips, InpMaxSpreadPips);
      lastLog = TimeCurrent();
   }
   return false;
}

double EnsureValidTP(int orderType, double priceNow, double rawTP)
{
   if(!InpEnforceMinStopDistTP) return NormalizeDouble(rawTP, g_digits);

   int stopLevelPts = (int)MarketInfo(Symbol(), MODE_STOPLEVEL);
   double minDist = stopLevelPts * g_point;
   double tp = rawTP;

   if(orderType == OP_BUY && tp - priceNow < minDist)
      tp = priceNow + MathMax(minDist, g_tpPoints);
   if(orderType == OP_SELL && priceNow - tp < minDist)
      tp = priceNow - MathMax(minDist, g_tpPoints);

   return NormalizeDouble(tp, g_digits);
}

void CountPositions(int &buyCount, int &sellCount, double &lowestBuy, double &highestSell)
{
   buyCount = 0;
   sellCount = 0;
   lowestBuy = 0.0;
   highestSell = 0.0;

   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
      if(OrderSymbol() != Symbol() || OrderMagicNumber() != InpMagicNumber) continue;

      if(OrderType() == OP_BUY)
      {
         buyCount++;
         if(lowestBuy == 0.0 || OrderOpenPrice() < lowestBuy)
            lowestBuy = OrderOpenPrice();
      }
      else if(OrderType() == OP_SELL)
      {
         sellCount++;
         if(highestSell == 0.0 || OrderOpenPrice() > highestSell)
            highestSell = OrderOpenPrice();
      }
   }
}

double BasketProfitBySide(int orderType)
{
   double total = 0.0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
      if(OrderSymbol() != Symbol() || OrderMagicNumber() != InpMagicNumber) continue;
      if(OrderType() != orderType) continue;
      total += OrderProfit() + OrderSwap() + OrderCommission();
   }
   return total;
}

bool CloseSide(int orderType)
{
   bool allClosed = true;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
      if(OrderSymbol() != Symbol() || OrderMagicNumber() != InpMagicNumber) continue;
      if(OrderType() != orderType) continue;

      RefreshRates();
      double price = (orderType == OP_BUY) ? Bid : Ask;
      if(!OrderClose(OrderTicket(), OrderLots(), price, InpMaxSlippagePoints, clrWhite))
      {
         Print("Erro fechando ordem #", OrderTicket(), " code=", GetLastError());
         allClosed = false;
      }
   }
   return allClosed;
}

void CloseBasketIfNeeded()
{
   double buyProfit = BasketProfitBySide(OP_BUY);
   if(buyProfit >= InpBasketTakeProfitMoney)
   {
      PrintFormat("Basket BUY atingiu lucro %.2f. Fechando lado.", buyProfit);
      CloseSide(OP_BUY);
   }

   double sellProfit = BasketProfitBySide(OP_SELL);
   if(sellProfit >= InpBasketTakeProfitMoney)
   {
      PrintFormat("Basket SELL atingiu lucro %.2f. Fechando lado.", sellProfit);
      CloseSide(OP_SELL);
   }
}

double EmaSlopeAtr()
{
   if(InpSlopeLookbackBars <= 0) return 0.0;

   double emaNow = iMA(Symbol(), Period(), InpEmaPeriod, 0, MODE_EMA, PRICE_CLOSE, 0);
   double emaPrev = iMA(Symbol(), Period(), InpEmaPeriod, 0, MODE_EMA, PRICE_CLOSE, InpSlopeLookbackBars);
   double atr = iATR(Symbol(), Period(), InpAtrPeriod, 0);

   if(emaNow <= 0 || emaPrev <= 0 || atr <= 0) return 0.0;
   return (emaNow - emaPrev) / atr;
}

bool CanSendOrder()
{
   datetime now = TimeCurrent();
   if(now < s_lastOrderTime + InpMinSecondsBetweenOrders) return false;

   if(InpOneOrderPerBar)
   {
      datetime barTime = iTime(Symbol(), Period(), 0);
      if(s_lastBarTime == barTime) return false;
   }
   return true;
}

bool SendBuy()
{
   RefreshRates();
   double lot = ValidLot(InpLotSize);
   double tp = EnsureValidTP(OP_BUY, Ask, Ask + g_tpPoints);
   int ticket = OrderSend(Symbol(), OP_BUY, lot, Ask, InpMaxSlippagePoints, 0, tp,
                          "EMA slope grid BUY", InpMagicNumber, 0, clrGreen);
   if(ticket < 0)
   {
      Print("Erro BUY code=", GetLastError());
      return false;
   }

   s_lastOrderTime = TimeCurrent();
   s_lastBarTime = iTime(Symbol(), Period(), 0);
   Print("BUY OK #", ticket, " lot=", DoubleToString(lot, 2), " tp=", DoubleToString(tp, g_digits));
   return true;
}

bool SendSell()
{
   RefreshRates();
   double lot = ValidLot(InpLotSize);
   double tp = EnsureValidTP(OP_SELL, Bid, Bid - g_tpPoints);
   int ticket = OrderSend(Symbol(), OP_SELL, lot, Bid, InpMaxSlippagePoints, 0, tp,
                          "EMA slope grid SELL", InpMagicNumber, 0, clrRed);
   if(ticket < 0)
   {
      Print("Erro SELL code=", GetLastError());
      return false;
   }

   s_lastOrderTime = TimeCurrent();
   s_lastBarTime = iTime(Symbol(), Period(), 0);
   Print("SELL OK #", ticket, " lot=", DoubleToString(lot, 2), " tp=", DoubleToString(tp, g_digits));
   return true;
}

//========================= Ciclo de Vida ============================
int OnInit()
{
   g_point = MarketInfo(Symbol(), MODE_POINT);
   g_digits = (int)MarketInfo(Symbol(), MODE_DIGITS);
   if(g_point <= 0 || g_digits <= 0)
   {
      Print("Simbolo invalido.");
      return INIT_FAILED;
   }

   g_pipMult = (g_digits == 3 || g_digits == 5) ? 10.0 : 1.0;
   g_gridPoints = InpGridSpacingPips * PipValue();
   g_tpPoints = InpTakeProfitPips * PipValue();

   if(InpMaxOrdersPerSide <= 0)
   {
      Print("InpMaxOrdersPerSide precisa ser maior que zero.");
      return INIT_FAILED;
   }
   if(InpGridSpacingPips <= 0 || InpTakeProfitPips <= 0)
   {
      Print("Grid e TP precisam ser maiores que zero.");
      return INIT_FAILED;
   }

   Print("EMA_Slope_Grid_Aggressive iniciado. Magic=", InpMagicNumber,
         " EMA=", InpEmaPeriod,
         " slopeLookback=", InpSlopeLookbackBars,
         " minSlopeATR=", DoubleToString(InpMinSlopeAtr, 3),
         " grid=", InpGridSpacingPips,
         " TP=", InpTakeProfitPips,
         " maxOrders=", InpMaxOrdersPerSide,
         " sem SL.");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   Print("EMA_Slope_Grid_Aggressive finalizado. reason=", reason);
}

void OnTick()
{
   datetime now = TimeCurrent();
   if(now == s_lastTickTime) return;
   s_lastTickTime = now;

   RefreshRates();
   if(Ask <= 0 || Bid <= 0 || Ask <= Bid) return;

   CloseBasketIfNeeded();

   if(!SpreadOk()) return;
   if(!CanSendOrder()) return;

   int buyCount, sellCount;
   double lowestBuy, highestSell;
   CountPositions(buyCount, sellCount, lowestBuy, highestSell);

   // Uma direcao por vez. Se existe BUY aberto, nao abre SELL, e vice-versa.
   if(buyCount > 0)
   {
      if(buyCount >= InpMaxOrdersPerSide) return;
      if(Bid <= lowestBuy - g_gridPoints)
      {
         PrintFormat("Grid BUY contra: Bid %.5f <= lowestBuy %.5f - grid.", Bid, lowestBuy);
         SendBuy();
      }
      return;
   }

   if(sellCount > 0)
   {
      if(sellCount >= InpMaxOrdersPerSide) return;
      if(Ask >= highestSell + g_gridPoints)
      {
         PrintFormat("Grid SELL contra: Ask %.5f >= highestSell %.5f + grid.", Ask, highestSell);
         SendSell();
      }
      return;
   }

   double slope = EmaSlopeAtr();
   if(slope >= InpMinSlopeAtr)
   {
      PrintFormat("Sinal BUY: slopeATR=%.3f", slope);
      SendBuy();
   }
   else if(slope <= -InpMinSlopeAtr)
   {
      PrintFormat("Sinal SELL: slopeATR=%.3f", slope);
      SendSell();
   }
}
//+------------------------------------------------------------------+
