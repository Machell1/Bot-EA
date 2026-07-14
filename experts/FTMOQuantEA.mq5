//+------------------------------------------------------------------+
//|                                               FTMOQuantEA.mq5     |
//|  Donchian/EMA trend EA with account-level FTMO 2-Step controls.  |
//|  No strategy or EA can guarantee a challenge pass.               |
//+------------------------------------------------------------------+
#property copyright "Bot-EA contributors"
#property version   "1.00"
#property strict
#property description "Conservative trend breakout EA with FTMO 2-Step risk guards"

#include <Trade/Trade.mqh>

input group "Strategy"
input ENUM_TIMEFRAMES InpSignalTimeframe       = PERIOD_H1;
input int             InpDonchianLookback      = 20;
input int             InpFastEmaPeriod         = 50;
input int             InpSlowEmaPeriod         = 200;
input int             InpAtrPeriod             = 14;
input double          InpStopAtrMultiple       = 2.5;
input double          InpRewardRisk            = 2.2;
input double          InpEntryBufferAtr        = 0.5;
input double          InpCandleBodyMin         = 0.2;
input double          InpCandleWickMax         = 0.3;
input bool            InpAllowLong             = true;
input bool            InpAllowShort            = true;

input group "Confluence & regime filters"
input bool            InpUseHtf1Confluence     = true;         // primary higher timeframe
input ENUM_TIMEFRAMES InpHtf1Timeframe         = PERIOD_H4;
input bool            InpUseHtf2Confluence     = true;         // secondary higher timeframe
input ENUM_TIMEFRAMES InpHtf2Timeframe         = PERIOD_D1;
input int             InpHtfFastEmaPeriod       = 50;
input int             InpHtfSlowEmaPeriod       = 200;
input int             InpSkipHour1             = 12;           // server hour to skip (-1 = none)
input int             InpVolAvgLen             = 50;           // ATR average length (0 = off)
input double          InpVolRatioMin           = 0.0;          // min ATR/avg at entry
input double          InpVolRatioMax           = 2.5;          // max ATR/avg at entry
input int             InpMsChannelLookback     = 0;            // market-structure lookback (0 = off)

input group "Position risk"
input double          InpRiskPerTradePct       = 0.35;
input int             InpMaxTradesPerDay       = 2;
input int             InpMaxLosingTradesPerDay = 2;
input double          InpMaxSpreadPoints       = 25.0;
input long            InpMagicNumber           = 26071401;
input int             InpSlippagePoints        = 10;

input group "FTMO 2-Step account guard"
input double          InpChallengeInitialBalance = 0.0;
input double          InpOfficialDailyLossPct    = 5.0;
input double          InpOfficialTotalLossPct    = 10.0;
input double          InpSoftDailyLossPct        = 4.0;
input double          InpSoftTotalLossPct        = 8.0;
input double          InpDailyProfitLockPct       = 1.0;
input int             InpDailyResetHourServer    = 0;
input int             InpDailyResetMinuteServer  = 0;
input bool            InpEmergencyCloseAllAccountPositions = true;
input string          InpStateId                  = "ftmo1";

input group "Trading window (server time)"
input int             InpSessionStartHour      = 8;
input int             InpSessionEndHour        = 17;
input bool            InpCloseBeforeWeekend    = true;
input int             InpFridayCloseHour       = 17;

input group "Trade management"
input double          InpBreakEvenAtR          = 1.0;
input double          InpTrailStartAtR         = 1.5;
input double          InpTrailAtrMultiple      = 2.0;

input group "Scale-out & trend pyramiding"
input double          InpTp1R                  = 1.0;   // first partial target (R)
input double          InpTp1Fraction           = 0.4;   // fraction closed at TP1
input double          InpTp2Fraction           = 0.3;   // fraction closed at TP2 (=InpRewardRisk)
input bool            InpPyramidEnabled        = true;  // add on pullbacks while trend holds
input int             InpMaxUnits              = 3;     // max concurrent units
input double          InpPullbackAtr           = 0.5;   // pullback depth (ATR) to arm a re-entry

input group "Post-fill lower-timeframe monitor"
input bool            InpMonitorEnabled        = true;  // watch M5/M15 while a unit is open
input int             InpMonitorMode           = 0;     // 0 = warn only, 1 = act (move stops to BE on ACTION)
input int             InpMonitorRsiPeriod      = 14;
input int             InpMonitorFastEma        = 8;
input int             InpMonitorSlowEma        = 21;
input int             InpMonitorDivLookback    = 6;     // bars back for price/RSI slope divergence
input int             InpMonitorHorizonMin     = 10;    // variance projection horizon (minutes)
input int             InpMonitorVarWindow      = 20;    // M5 bars for return-volatility estimate
input double          InpMonitorVarZ           = 2.0;   // projection band width in sigmas

CTrade trade;
int    fastEmaHandle = INVALID_HANDLE;
int    slowEmaHandle = INVALID_HANDLE;
int    atrHandle     = INVALID_HANDLE;
int    htf1FastHandle = INVALID_HANDLE;
int    htf1SlowHandle = INVALID_HANDLE;
int    htf2FastHandle = INVALID_HANDLE;
int    htf2SlowHandle = INVALID_HANDLE;
int    m5RsiHandle = INVALID_HANDLE;
int    m15RsiHandle = INVALID_HANDLE;
int    m5FastHandle = INVALID_HANDLE;
int    m5SlowHandle = INVALID_HANDLE;
int    m15FastHandle = INVALID_HANDLE;
int    m15SlowHandle = INVALID_HANDLE;
datetime lastMonitorBar = 0;
double initialBalance = 0.0;
double dayStartBalance = 0.0;
int    currentTradingDay = 0;
bool   tradingLocked = false;
bool   emergencyMode = false;
datetime lastSignalBar = 0;
string statePrefix;

//+------------------------------------------------------------------+
//| Persistent state helpers                                         |
//+------------------------------------------------------------------+
string StateKey(const string suffix)
{
   return statePrefix + suffix;
}

uint StringHash(const string value)
{
   uint hash = 2166136261;
   for(int i = 0; i < StringLen(value); ++i)
   {
      hash ^= (uint)StringGetCharacter(value, i);
      hash *= 16777619;
   }
   return hash;
}

int TradingDayId(const datetime now)
{
   datetime shifted = now - InpDailyResetHourServer * 3600
                           - InpDailyResetMinuteServer * 60;
   MqlDateTime parts;
   TimeToStruct(shifted, parts);
   return parts.year * 10000 + parts.mon * 100 + parts.day;
}

datetime TradingDayStart(const datetime now)
{
   MqlDateTime parts;
   TimeToStruct(now, parts);
   parts.hour = InpDailyResetHourServer;
   parts.min  = InpDailyResetMinuteServer;
   parts.sec  = 0;
   datetime start = StructToTime(parts);
   if(now < start)
      start -= 86400;
   return start;
}

void RefreshDailyState()
{
   datetime now = TimeTradeServer();
   int dayId = TradingDayId(now);
   if(dayId == currentTradingDay)
      return;

   // Reconstruct the balance at reset even when the first chart tick is late.
   datetime start = TradingDayStart(now);
   double balanceDelta = 0.0;
   if(!HistorySelect(start, now))
   {
      tradingLocked = true;
      Print("Cannot reconstruct the FTMO reset balance; entries remain locked");
      return;
   }
   int deals = HistoryDealsTotal();
   for(int i = 0; i < deals; ++i)
   {
      ulong ticket = HistoryDealGetTicket(i);
      balanceDelta += HistoryDealGetDouble(ticket, DEAL_PROFIT);
      balanceDelta += HistoryDealGetDouble(ticket, DEAL_SWAP);
      balanceDelta += HistoryDealGetDouble(ticket, DEAL_COMMISSION);
      balanceDelta += HistoryDealGetDouble(ticket, DEAL_FEE);
   }

   currentTradingDay = dayId;
   dayStartBalance = AccountInfoDouble(ACCOUNT_BALANCE) - balanceDelta;
   tradingLocked = false;
   if(emergencyMode)
      tradingLocked = true;
   GlobalVariableSet(StateKey("day_id"), (double)currentTradingDay);
   GlobalVariableSet(StateKey("day_balance"), dayStartBalance);
   GlobalVariableSet(StateKey("lock_day"),
                     tradingLocked ? (double)currentTradingDay : 0.0);
}

void LoadState()
{
   uint serverHash = StringHash(AccountInfoString(ACCOUNT_SERVER));
   statePrefix = "FTMOQ_" + StringFormat("%I64d", AccountInfoInteger(ACCOUNT_LOGIN))
               + "_" + IntegerToString((int)serverHash) + "_"
               + InpStateId + "_";

   if(InpChallengeInitialBalance > 0.0)
   {
      initialBalance = InpChallengeInitialBalance;
      GlobalVariableSet(StateKey("initial"), initialBalance);
   }
   else if(GlobalVariableCheck(StateKey("initial")))
      initialBalance = GlobalVariableGet(StateKey("initial"));
   else
   {
      initialBalance = AccountInfoDouble(ACCOUNT_BALANCE);
      GlobalVariableSet(StateKey("initial"), initialBalance);
   }

   if(GlobalVariableCheck(StateKey("emergency_active")) &&
      GlobalVariableGet(StateKey("emergency_active")) > 0.0)
      emergencyMode = true;

   int today = TradingDayId(TimeTradeServer());
   if(GlobalVariableCheck(StateKey("day_id")) &&
      (int)GlobalVariableGet(StateKey("day_id")) == today &&
      GlobalVariableCheck(StateKey("day_balance")))
   {
      currentTradingDay = today;
      dayStartBalance = GlobalVariableGet(StateKey("day_balance"));
      if(GlobalVariableCheck(StateKey("lock_day")) &&
         (int)GlobalVariableGet(StateKey("lock_day")) == today)
         tradingLocked = true;
      if(emergencyMode)
         tradingLocked = true;
   }
   else
   {
      currentTradingDay = 0;
      RefreshDailyState();
   }
}

//+------------------------------------------------------------------+
//| Account compliance                                               |
//+------------------------------------------------------------------+
double DailyOfficialFloor()
{
   return dayStartBalance - initialBalance * InpOfficialDailyLossPct / 100.0;
}

double TotalOfficialFloor()
{
   return initialBalance * (1.0 - InpOfficialTotalLossPct / 100.0);
}

double DailySoftFloor()
{
   return dayStartBalance - initialBalance * InpSoftDailyLossPct / 100.0;
}

double TotalSoftFloor()
{
   return initialBalance * (1.0 - InpSoftTotalLossPct / 100.0);
}

double ActiveProtectionFloor()
{
   return MathMax(MathMax(DailyOfficialFloor(), TotalOfficialFloor()),
                  MathMax(DailySoftFloor(), TotalSoftFloor()));
}

double TodayClosedPnl()
{
   datetime start = TradingDayStart(TimeTradeServer());
   if(!HistorySelect(start, TimeTradeServer()))
      return 0.0;

   double pnl = 0.0;
   int total = HistoryDealsTotal();
   for(int i = 0; i < total; ++i)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0)
         continue;
      pnl += HistoryDealGetDouble(ticket, DEAL_PROFIT);
      pnl += HistoryDealGetDouble(ticket, DEAL_SWAP);
      pnl += HistoryDealGetDouble(ticket, DEAL_COMMISSION);
      pnl += HistoryDealGetDouble(ticket, DEAL_FEE);
   }
   return pnl;
}

void TodayStrategyStats(int &entries, int &losses)
{
   entries = 0;
   losses = 0;
   datetime start = TradingDayStart(TimeTradeServer());
   if(!HistorySelect(start, TimeTradeServer()))
      return;

   int total = HistoryDealsTotal();
   for(int i = 0; i < total; ++i)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0 ||
         HistoryDealGetInteger(ticket, DEAL_MAGIC) != InpMagicNumber)
         continue;

      ENUM_DEAL_ENTRY entry =
         (ENUM_DEAL_ENTRY)HistoryDealGetInteger(ticket, DEAL_ENTRY);
      if(entry == DEAL_ENTRY_IN || entry == DEAL_ENTRY_INOUT)
         entries++;

      if(entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_OUT_BY ||
         entry == DEAL_ENTRY_INOUT)
      {
         double pnl = HistoryDealGetDouble(ticket, DEAL_PROFIT)
                    + HistoryDealGetDouble(ticket, DEAL_SWAP)
                    + HistoryDealGetDouble(ticket, DEAL_COMMISSION)
                    + HistoryDealGetDouble(ticket, DEAL_FEE);
         if(pnl < 0.0)
            losses++;
      }
   }
}

void EmergencyFlatten(const string reason)
{
   tradingLocked = true;
   emergencyMode = true;
   GlobalVariableSet(StateKey("lock_day"), (double)currentTradingDay);
   GlobalVariableSet(StateKey("emergency_active"), 1.0);
   Print("FTMO risk lock: ", reason, ". Retrying liquidation until flat.");
   int total = PositionsTotal();
   for(int i = total - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      long magic = PositionGetInteger(POSITION_MAGIC);
      if(InpEmergencyCloseAllAccountPositions || magic == InpMagicNumber)
      {
         bool requested = trade.PositionClose(ticket);
         uint retcode = trade.ResultRetcode();
         if(!requested ||
            (retcode != TRADE_RETCODE_DONE &&
             retcode != TRADE_RETCODE_DONE_PARTIAL))
            Print("Emergency close failed for #", ticket, ": ",
                  trade.ResultRetcodeDescription());
      }
   }

   bool targetsRemain = false;
   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0 &&
         (InpEmergencyCloseAllAccountPositions ||
          PositionGetInteger(POSITION_MAGIC) == InpMagicNumber))
      {
         targetsRemain = true;
         break;
      }
   }
   emergencyMode = targetsRemain;
   GlobalVariableSet(StateKey("emergency_active"), targetsRemain ? 1.0 : 0.0);
}

bool CheckAccountGuard()
{
   RefreshDailyState();
   if(emergencyMode)
   {
      EmergencyFlatten("protection mode remains active");
      return false;
   }
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double floor = ActiveProtectionFloor();

   if(equity <= floor)
   {
      EmergencyFlatten("equity reached the configured protection floor");
      return false;
   }

   // A modest daily gain lock reduces give-back and Best Day concentration.
   if(InpDailyProfitLockPct > 0.0 &&
      TodayClosedPnl() >= initialBalance * InpDailyProfitLockPct / 100.0)
   {
      tradingLocked = true;
      GlobalVariableSet(StateKey("lock_day"), (double)currentTradingDay);
      return false;
   }
   return !tradingLocked;
}

double PointValuePerLot()
{
   double ts = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   if(ts <= 0.0)
      return 0.0;
   return tv / ts;
}

int CountUnits()
{
   int n = 0;
   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0 &&
         PositionGetInteger(POSITION_MAGIC) == InpMagicNumber &&
         PositionGetString(POSITION_SYMBOL) == _Symbol)
         n++;
   }
   return n;
}

// Sum of the money still at risk to each unit's stop; used to keep aggregate
// pyramiding exposure inside the FTMO floor.
double OpenRiskMoney()
{
   double total = 0.0;
   double vpl = PointValuePerLot();
   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 ||
         PositionGetInteger(POSITION_MAGIC) != InpMagicNumber ||
         PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      double open = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl = PositionGetDouble(POSITION_SL);
      double vol = PositionGetDouble(POSITION_VOLUME);
      ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      double riskPrice = type == POSITION_TYPE_BUY ? open - sl : sl - open;
      if(riskPrice > 0.0)
         total += riskPrice * vol * vpl;
   }
   return total;
}

bool AnyUnitTp1Done()
{
   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 ||
         PositionGetInteger(POSITION_MAGIC) != InpMagicNumber ||
         PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      if(GlobalVariableGet(StateKey("f_" + StringFormat("%I64u", ticket))) >= 1.0)
         return true;
   }
   return false;
}

bool ProjectedRiskAllowed(const double riskMoney)
{
   double projectedEquity = AccountInfoDouble(ACCOUNT_EQUITY)
                          - (OpenRiskMoney() + riskMoney) * 1.15;
   return projectedEquity > ActiveProtectionFloor();
}

int TrendSide()          { return (int)GlobalVariableGet(StateKey("trend_side")); }
void SetTrendSide(int s) { GlobalVariableSet(StateKey("trend_side"), (double)s); }
bool PullbackArmed()     { return GlobalVariableGet(StateKey("pullback")) > 0.0; }
void SetPullback(bool a) { GlobalVariableSet(StateKey("pullback"), a ? 1.0 : 0.0); }

void ResetTrendExtreme()
{
   GlobalVariableSet(StateKey("trend_ext"),
                     iClose(_Symbol, InpSignalTimeframe, 1));
}

// Fold the last completed bar into the favorable extreme and arm a re-entry
// once price has retraced by InpPullbackAtr * ATR.
void UpdatePullback()
{
   int side = TrendSide();
   if(side == 0)
      return;
   double atr = IndicatorValue(atrHandle, 1);
   if(atr == EMPTY_VALUE || atr <= 0.0)
      return;
   double hi1 = iHigh(_Symbol, InpSignalTimeframe, 1);
   double lo1 = iLow(_Symbol, InpSignalTimeframe, 1);
   double ext = GlobalVariableGet(StateKey("trend_ext"));
   if(side > 0)
   {
      if(hi1 > ext)
      {
         ext = hi1;
         GlobalVariableSet(StateKey("trend_ext"), ext);
      }
      if(ext - lo1 >= InpPullbackAtr * atr)
         SetPullback(true);
   }
   else
   {
      if(ext == 0.0 || lo1 < ext)
      {
         ext = lo1;
         GlobalVariableSet(StateKey("trend_ext"), ext);
      }
      if(hi1 - ext >= InpPullbackAtr * atr)
         SetPullback(true);
   }
}

void CloseAllUnits(const string reason)
{
   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 ||
         PositionGetInteger(POSITION_MAGIC) != InpMagicNumber ||
         PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      if(!trade.PositionClose(ticket))
         Print("Close-all (", reason, ") failed for #", ticket, ": ",
               trade.ResultRetcodeDescription());
   }
   SetTrendSide(0);
   SetPullback(false);
}

//+------------------------------------------------------------------+
//| Strategy helpers                                                 |
//+------------------------------------------------------------------+
double IndicatorValue(const int handle, const int shift)
{
   double value[1];
   if(CopyBuffer(handle, 0, shift, 1, value) != 1)
      return EMPTY_VALUE;
   return value[0];
}

bool IsTradingSession()
{
   MqlDateTime now;
   TimeToStruct(TimeTradeServer(), now);
   if(now.day_of_week == 0 || now.day_of_week == 6)
      return false;
   if(now.day_of_week == 5 && now.hour >= InpFridayCloseHour)
      return false;
   if(InpSkipHour1 >= 0 && now.hour == InpSkipHour1)
      return false;

   if(InpSessionStartHour == InpSessionEndHour)
      return true;
   if(InpSessionStartHour < InpSessionEndHour)
      return now.hour >= InpSessionStartHour && now.hour < InpSessionEndHour;
   return now.hour >= InpSessionStartHour || now.hour < InpSessionEndHour;
}

bool HtfAgrees(const int fastHandle, const int slowHandle, const int direction)
{
   double f = IndicatorValue(fastHandle, 1);
   double s = IndicatorValue(slowHandle, 1);
   if(f == EMPTY_VALUE || s == EMPTY_VALUE)
      return false;
   return direction > 0 ? f > s : f < s;
}

// Higher-timeframe confluence, volatility regime and market-structure gates.
// Each references only completed bars (shift 1) to avoid look-ahead.
bool ConfluenceOK(const int direction)
{
   if(InpUseHtf1Confluence && !HtfAgrees(htf1FastHandle, htf1SlowHandle, direction))
      return false;
   if(InpUseHtf2Confluence && !HtfAgrees(htf2FastHandle, htf2SlowHandle, direction))
      return false;

   if(InpVolAvgLen > 0)
   {
      double atrBuf[];
      if(CopyBuffer(atrHandle, 0, 1, InpVolAvgLen, atrBuf) != InpVolAvgLen)
         return false;
      double sum = 0.0;
      for(int i = 0; i < InpVolAvgLen; ++i)
         sum += atrBuf[i];
      double avg = sum / InpVolAvgLen;
      double atr1 = IndicatorValue(atrHandle, 1);
      if(avg <= 0.0 || atr1 == EMPTY_VALUE)
         return false;
      double ratio = atr1 / avg;
      if(ratio < InpVolRatioMin || ratio > InpVolRatioMax)
         return false;
   }

   if(InpMsChannelLookback > 0)
   {
      int lb = InpMsChannelLookback;
      double upNow = -DBL_MAX, upThen = -DBL_MAX, loNow = DBL_MAX, loThen = DBL_MAX;
      for(int shift = 2; shift < InpDonchianLookback + 2; ++shift)
      {
         upNow  = MathMax(upNow,  iHigh(_Symbol, InpSignalTimeframe, shift));
         loNow  = MathMin(loNow,  iLow(_Symbol, InpSignalTimeframe, shift));
         upThen = MathMax(upThen, iHigh(_Symbol, InpSignalTimeframe, shift + lb));
         loThen = MathMin(loThen, iLow(_Symbol, InpSignalTimeframe, shift + lb));
      }
      if(direction > 0 && !(upNow > upThen && loNow > loThen))
         return false;
      if(direction < 0 && !(upNow < upThen && loNow < loThen))
         return false;
   }
   return true;
}

int Signal()
{
   if(Bars(_Symbol, InpSignalTimeframe) <
      InpSlowEmaPeriod + InpDonchianLookback + 10)
      return 0;

   double fast1 = IndicatorValue(fastEmaHandle, 1);
   double fast2 = IndicatorValue(fastEmaHandle, 2);
   double slow1 = IndicatorValue(slowEmaHandle, 1);
   if(fast1 == EMPTY_VALUE || fast2 == EMPTY_VALUE || slow1 == EMPTY_VALUE)
      return 0;

   double upper = -DBL_MAX;
   double lower = DBL_MAX;
   for(int shift = 2; shift < InpDonchianLookback + 2; ++shift)
   {
      upper = MathMax(upper, iHigh(_Symbol, InpSignalTimeframe, shift));
      lower = MathMin(lower, iLow(_Symbol, InpSignalTimeframe, shift));
   }

   // Require the breakout close to clear the channel by a fraction of ATR so
   // marginal pokes through the range (the main source of whipsaw) are ignored.
   double buffer = 0.0;
   if(InpEntryBufferAtr > 0.0)
   {
      double atr1 = IndicatorValue(atrHandle, 1);
      if(atr1 == EMPTY_VALUE || atr1 <= 0.0)
         return 0;
      buffer = InpEntryBufferAtr * atr1;
   }

   // Candlestick confirmation on the completed breakout bar (shift 1): demand a
   // decisive body that closes in the breakout direction with only a small
   // rejection wick. A doji or a long opposing wick means the range was
   // defended, so skip the trade.
   double open1  = iOpen(_Symbol, InpSignalTimeframe, 1);
   double high1  = iHigh(_Symbol, InpSignalTimeframe, 1);
   double low1   = iLow(_Symbol, InpSignalTimeframe, 1);
   double close1 = iClose(_Symbol, InpSignalTimeframe, 1);
   double candleRange = high1 - low1;
   if(candleRange <= 0.0)
      return 0;
   double body      = MathAbs(close1 - open1);
   double upperWick = high1 - MathMax(open1, close1);
   double lowerWick = MathMin(open1, close1) - low1;
   bool strongBody  = body / candleRange >= InpCandleBodyMin;
   bool bullishCandle = strongBody && close1 > open1 &&
                        upperWick / candleRange <= InpCandleWickMax;
   bool bearishCandle = strongBody && close1 < open1 &&
                        lowerWick / candleRange <= InpCandleWickMax;

   if(InpAllowLong && close1 > upper + buffer && fast1 > slow1 && fast1 > fast2 &&
      bullishCandle && ConfluenceOK(1))
      return 1;
   if(InpAllowShort && close1 < lower - buffer && fast1 < slow1 && fast1 < fast2 &&
      bearishCandle && ConfluenceOK(-1))
      return -1;
   return 0;
}

double PositionSize(const int direction, const double entryPrice,
                    const double stopPrice, const double riskMoney)
{
   double lossPerLot = 0.0;
   ENUM_ORDER_TYPE orderType = direction > 0 ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   if(!OrderCalcProfit(orderType, _Symbol, 1.0, entryPrice, stopPrice,
                       lossPerLot) ||
      lossPerLot >= 0.0)
      return 0.0;

   // Leave part of the risk budget for commission, slippage and gaps.
   double volume = (riskMoney / 1.10) / MathAbs(lossPerLot);
   double minVolume = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxVolume = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0.0)
      return 0.0;

   volume = MathFloor(volume / step) * step;
   volume = MathMin(volume, maxVolume);
   if(volume < minVolume)
      return 0.0;
   return NormalizeDouble(volume, 8);
}

// Opens one trend unit with a stop only; TP1/TP2 partials and the runner trail
// are managed on each bar in ManagePositions. Per-ticket metadata is created
// lazily there on first sight, so no ticket lookup is needed here.
bool OpenUnit(const int direction)
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return false;

   double spreadPoints = (tick.ask - tick.bid) / _Point;
   if(spreadPoints > InpMaxSpreadPoints)
      return false;

   double atr = IndicatorValue(atrHandle, 1);
   if(atr == EMPTY_VALUE || atr <= 0.0)
      return false;

   long stopsLevelPoints = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double stopDistance = MathMax(atr * InpStopAtrMultiple,
                                 (stopsLevelPoints + 2) * _Point);
   double riskMoney = AccountInfoDouble(ACCOUNT_EQUITY)
                    * InpRiskPerTradePct / 100.0;
   if(!ProjectedRiskAllowed(riskMoney))
      return false;

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   bool sent = false;
   if(direction > 0)
   {
      double sl = NormalizeDouble(tick.bid - stopDistance, digits);
      double volume = PositionSize(direction, tick.ask, sl, riskMoney);
      if(volume <= 0.0)
         return false;
      sent = trade.Buy(volume, _Symbol, 0.0, sl, 0.0, "FTMOQ unit");
   }
   else
   {
      double sl = NormalizeDouble(tick.ask + stopDistance, digits);
      double volume = PositionSize(direction, tick.bid, sl, riskMoney);
      if(volume <= 0.0)
         return false;
      sent = trade.Sell(volume, _Symbol, 0.0, sl, 0.0, "FTMOQ unit");
   }

   uint retcode = trade.ResultRetcode();
   if(!sent || (retcode != TRADE_RETCODE_DONE &&
                retcode != TRADE_RETCODE_DONE_PARTIAL &&
                retcode != TRADE_RETCODE_PLACED))
   {
      Print("Order failed: ", trade.ResultRetcodeDescription());
      return false;
   }
   return true;
}

double FloorVolume(const double raw)
{
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0.0)
      return 0.0;
   return NormalizeDouble(MathFloor(raw / step) * step, 8);
}

//+------------------------------------------------------------------+
//| Open-position management: TP1/TP2 scale-out + runner trail       |
//+------------------------------------------------------------------+
void ManagePositions()
{
   double atr = IndicatorValue(atrHandle, 1);
   if(atr == EMPTY_VALUE || atr <= 0.0)
      return;

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return;

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   long stopLevelPoints = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double minStopDistance = (stopLevelPoints + 2) * _Point;
   double minVolume = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);

   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 ||
         PositionGetInteger(POSITION_MAGIC) != InpMagicNumber ||
         PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;

      ENUM_POSITION_TYPE type =
         (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      double open = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl = PositionGetDouble(POSITION_SL);
      double vol = PositionGetDouble(POSITION_VOLUME);
      if(sl <= 0.0)
         continue;

      string suffix = StringFormat("%I64u", ticket);
      string irKey = StateKey("ir_" + suffix);
      string ovKey = StateKey("ov_" + suffix);
      string t1Key = StateKey("t1_" + suffix);
      string t2Key = StateKey("t2_" + suffix);
      string fKey  = StateKey("f_" + suffix);

      double initialRisk, originalVol, tp1, tp2;
      int flags;
      if(!GlobalVariableCheck(irKey))
      {
         // First sight of this unit: capture its geometry (volume is still the
         // original, no partial has run yet) and its TP1/TP2 prices.
         initialRisk = MathAbs(open - sl);
         if(initialRisk <= 0.0)
            continue;
         originalVol = vol;
         tp1 = type == POSITION_TYPE_BUY ? open + initialRisk * InpTp1R
                                         : open - initialRisk * InpTp1R;
         tp2 = type == POSITION_TYPE_BUY ? open + initialRisk * InpRewardRisk
                                         : open - initialRisk * InpRewardRisk;
         flags = 0;
         GlobalVariableSet(irKey, initialRisk);
         GlobalVariableSet(ovKey, originalVol);
         GlobalVariableSet(t1Key, tp1);
         GlobalVariableSet(t2Key, tp2);
         GlobalVariableSet(fKey, 0.0);
      }
      else
      {
         initialRisk = GlobalVariableGet(irKey);
         originalVol = GlobalVariableGet(ovKey);
         tp1 = GlobalVariableGet(t1Key);
         tp2 = GlobalVariableGet(t2Key);
         flags = (int)GlobalVariableGet(fKey);
      }
      if(initialRisk <= 0.0)
         continue;

      // TP1: book the first partial and trail the original order to break-even.
      if(flags < 1)
      {
         bool hit = type == POSITION_TYPE_BUY ? tick.bid >= tp1 : tick.ask <= tp1;
         if(hit)
         {
            double closeVol = FloorVolume(originalVol * InpTp1Fraction);
            if(closeVol >= minVolume && closeVol < vol)
            {
               trade.PositionClosePartial(ticket, closeVol);
               if(PositionSelectByTicket(ticket))
                  vol = PositionGetDouble(POSITION_VOLUME);
            }
            flags = 1;
            GlobalVariableSet(fKey, 1.0);
         }
      }

      // TP2: book the second partial; the remainder rides as a trailing runner.
      if(flags == 1)
      {
         bool hit2 = type == POSITION_TYPE_BUY ? tick.bid >= tp2 : tick.ask <= tp2;
         if(hit2)
         {
            double closeVol = FloorVolume(originalVol * InpTp2Fraction);
            if(closeVol >= minVolume && closeVol < vol)
            {
               trade.PositionClosePartial(ticket, closeVol);
               if(PositionSelectByTicket(ticket))
                  vol = PositionGetDouble(POSITION_VOLUME);
            }
            flags = 2;
            GlobalVariableSet(fKey, 2.0);
         }
      }

      double move = type == POSITION_TYPE_BUY ? tick.bid - open : open - tick.ask;
      double candidate = sl;
      if(flags >= 1 || move >= initialRisk * InpBreakEvenAtR)
      {
         if(type == POSITION_TYPE_BUY)
            candidate = MathMax(candidate, open);
         else
            candidate = MathMin(candidate, open);
      }
      if(flags >= 2 && move >= initialRisk * InpTrailStartAtR)
      {
         if(type == POSITION_TYPE_BUY)
            candidate = MathMax(candidate, tick.bid - atr * InpTrailAtrMultiple);
         else
            candidate = MathMin(candidate, tick.ask + atr * InpTrailAtrMultiple);
      }

      if(type == POSITION_TYPE_BUY)
         candidate = MathMin(candidate, tick.bid - minStopDistance);
      else
         candidate = MathMax(candidate, tick.ask + minStopDistance);
      candidate = NormalizeDouble(candidate, digits);

      bool improves = type == POSITION_TYPE_BUY
                      ? candidate > sl + _Point
                      : candidate < sl - _Point;
      if(improves && PositionSelectByTicket(ticket))
      {
         if(!trade.PositionModify(ticket, candidate, 0.0))
         {
            uint retcode = trade.ResultRetcode();
            if(retcode != TRADE_RETCODE_NO_CHANGES)
               Print("Stop update failed for #", ticket, ": ",
                     trade.ResultRetcodeDescription());
         }
      }
   }
}

void HandleWeekendExit()
{
   if(!InpCloseBeforeWeekend)
      return;
   MqlDateTime now;
   TimeToStruct(TimeTradeServer(), now);
   if(now.day_of_week != 5 || now.hour < InpFridayCloseHour)
      return;

   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0 &&
         PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
      {
         bool requested = trade.PositionClose(ticket);
         uint retcode = trade.ResultRetcode();
         if(!requested ||
            (retcode != TRADE_RETCODE_DONE &&
             retcode != TRADE_RETCODE_DONE_PARTIAL))
            Print("Weekend close failed for #", ticket, ": ",
                  trade.ResultRetcodeDescription());
      }
   }
}

//+------------------------------------------------------------------+
//| Post-fill lower-timeframe monitor                                |
//+------------------------------------------------------------------+
double LowerTfReturnSigma(const ENUM_TIMEFRAMES tf, const int window)
{
   int need = window + 1;
   double closes[];
   if(CopyClose(_Symbol, tf, 1, need, closes) != need)
      return 0.0;
   int count = need - 1;
   double mean = 0.0;
   double rets[];
   ArrayResize(rets, count);
   int m = 0;
   for(int i = 1; i < need; ++i)
   {
      if(closes[i - 1] != 0.0)
      {
         rets[m] = (closes[i] - closes[i - 1]) / closes[i - 1];
         mean += rets[m];
         m++;
      }
   }
   if(m < 2)
      return 0.0;
   mean /= m;
   double var = 0.0;
   for(int i = 0; i < m; ++i)
      var += (rets[i] - mean) * (rets[i] - mean);
   return MathSqrt(var / (m - 1));
}

bool MonitorDivergence(const int rsiHandle, const ENUM_TIMEFRAMES tf,
                       const int side, const int look)
{
   double priceNow = iClose(_Symbol, tf, 1);
   double priceThen = iClose(_Symbol, tf, 1 + look);
   double rsiNow = IndicatorValue(rsiHandle, 1);
   double rsiThen = IndicatorValue(rsiHandle, 1 + look);
   if(rsiNow == EMPTY_VALUE || rsiThen == EMPTY_VALUE ||
      priceNow <= 0.0 || priceThen <= 0.0)
      return false;
   if(side > 0)
      return priceNow > priceThen && rsiNow < rsiThen;
   return priceNow < priceThen && rsiNow > rsiThen;
}

bool MonitorAligned(const int fastHandle, const int slowHandle, const int side)
{
   double f = IndicatorValue(fastHandle, 1);
   double s = IndicatorValue(slowHandle, 1);
   if(f == EMPTY_VALUE || s == EMPTY_VALUE)
      return true;  // insufficient data: do not raise a false misalignment
   return side > 0 ? f > s : f < s;
}

// 0 = OK, 1 = WARNING (close monitoring), 2 = ACTION (immediate).
int AssessLowerTf(const int side, const double stop, string &reason)
{
   bool div5 = MonitorDivergence(m5RsiHandle, PERIOD_M5, side, InpMonitorDivLookback);
   bool div15 = MonitorDivergence(m15RsiHandle, PERIOD_M15, side, InpMonitorDivLookback);
   bool aligned5 = MonitorAligned(m5FastHandle, m5SlowHandle, side);
   bool aligned15 = MonitorAligned(m15FastHandle, m15SlowHandle, side);

   double price = iClose(_Symbol, PERIOD_M5, 1);
   double sigma = LowerTfReturnSigma(PERIOD_M5, InpMonitorVarWindow);
   int steps = MathMax(1, InpMonitorHorizonMin / 5);
   double move = InpMonitorVarZ * sigma * MathSqrt((double)steps) * price;
   double adverse = side > 0 ? price - move : price + move;
   bool breach = side > 0 ? adverse <= stop : adverse >= stop;

   reason = "";
   if(div5)   reason += "M5 divergence; ";
   if(div15)  reason += "M15 divergence; ";
   if(!aligned5)  reason += "M5 EMA against trend; ";
   if(!aligned15) reason += "M15 EMA against trend; ";
   if(breach) reason += "projected 10m variance reaches stop; ";

   bool threat = div5 || div15 || breach;
   if(!aligned15 && threat)
      return 2;
   if(div5 || div15 || breach || !aligned5)
      return 1;
   reason = "aligned with H1; variance within tolerance";
   return 0;
}

void MoveAllStopsToBreakEven()
{
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 ||
         PositionGetInteger(POSITION_MAGIC) != InpMagicNumber ||
         PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      double open = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl = PositionGetDouble(POSITION_SL);
      ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      double be = NormalizeDouble(open, digits);
      bool improves = type == POSITION_TYPE_BUY ? be > sl + _Point : be < sl - _Point;
      if(improves && PositionSelectByTicket(ticket))
         trade.PositionModify(ticket, be, 0.0);
   }
}

void RunLowerTfMonitor()
{
   if(!InpMonitorEnabled || CountUnits() == 0)
      return;
   int side = TrendSide();
   if(side == 0)
      return;
   datetime m5bar = iTime(_Symbol, PERIOD_M5, 0);
   if(m5bar <= 0 || m5bar == lastMonitorBar)
      return;
   lastMonitorBar = m5bar;

   // Watch the stop most likely to be hit first (nearest to price).
   double nearest = 0.0;
   bool have = false;
   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 ||
         PositionGetInteger(POSITION_MAGIC) != InpMagicNumber ||
         PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      double sl = PositionGetDouble(POSITION_SL);
      if(sl <= 0.0)
         continue;
      if(!have)
      {
         nearest = sl;
         have = true;
      }
      else
         nearest = side > 0 ? MathMax(nearest, sl) : MathMin(nearest, sl);
   }
   if(!have)
      return;

   string reason;
   int status = AssessLowerTf(side, nearest, reason);
   if(status == 2)
   {
      Comment("FTMOQ lower-TF monitor: ACTION - ", reason);
      Print("Lower-TF ACTION: ", reason);
      if(InpMonitorMode == 1)
         MoveAllStopsToBreakEven();
   }
   else if(status == 1)
   {
      Comment("FTMOQ lower-TF monitor: WARNING - ", reason);
      Print("Lower-TF WARNING: ", reason);
   }
   else
      Comment("FTMOQ lower-TF monitor: OK");
}

//+------------------------------------------------------------------+
//| Expert lifecycle                                                 |
//+------------------------------------------------------------------+
int OnInit()
{
   if(InpDonchianLookback < 2 || InpFastEmaPeriod < 2 ||
      InpSlowEmaPeriod <= InpFastEmaPeriod || InpAtrPeriod < 2 ||
      InpRiskPerTradePct <= 0.0 || InpStopAtrMultiple <= 0.0 ||
      InpRewardRisk <= 0.0 || InpEntryBufferAtr < 0.0 ||
      InpCandleBodyMin < 0.0 || InpCandleBodyMin > 1.0 ||
      InpCandleWickMax < 0.0 || InpCandleWickMax > 1.0 ||
      InpHtfFastEmaPeriod < 2 || InpHtfSlowEmaPeriod <= InpHtfFastEmaPeriod ||
      InpSkipHour1 > 23 || InpVolAvgLen < 0 ||
      InpVolRatioMin < 0.0 || InpVolRatioMax < InpVolRatioMin ||
      InpMsChannelLookback < 0 ||
      InpTp1R <= 0.0 || InpTp1R >= InpRewardRisk ||
      InpTp1Fraction < 0.0 || InpTp2Fraction < 0.0 ||
      InpTp1Fraction + InpTp2Fraction > 1.0 ||
      InpMaxUnits < 1 || InpPullbackAtr < 0.0 ||
      InpMonitorMode < 0 || InpMonitorMode > 1 ||
      InpMonitorRsiPeriod < 2 || InpMonitorFastEma < 2 ||
      InpMonitorSlowEma <= InpMonitorFastEma ||
      InpMonitorDivLookback < 1 || InpMonitorHorizonMin < 1 ||
      InpMonitorVarWindow < 2 || InpMonitorVarZ < 0.0 ||
      InpMaxTradesPerDay < 1 ||
      InpMaxLosingTradesPerDay < 1 || InpMaxSpreadPoints < 0.0 ||
      InpSlippagePoints < 0 || InpBreakEvenAtR <= 0.0 ||
      InpTrailStartAtR < InpBreakEvenAtR || InpTrailAtrMultiple <= 0.0 ||
      InpChallengeInitialBalance < 0.0 ||
      InpOfficialDailyLossPct <= 0.0 || InpOfficialDailyLossPct > 100.0 ||
      InpOfficialTotalLossPct <= 0.0 || InpOfficialTotalLossPct > 100.0 ||
      InpSoftDailyLossPct <= 0.0 ||
      InpSoftDailyLossPct >= InpOfficialDailyLossPct ||
      InpSoftTotalLossPct <= 0.0 ||
      InpSoftTotalLossPct >= InpOfficialTotalLossPct ||
      InpDailyProfitLockPct < 0.0 || InpDailyProfitLockPct > 100.0 ||
      InpDailyResetHourServer < 0 || InpDailyResetHourServer > 23 ||
      InpDailyResetMinuteServer < 0 || InpDailyResetMinuteServer > 59 ||
      InpSessionStartHour < 0 || InpSessionStartHour > 23 ||
      InpSessionEndHour < 0 || InpSessionEndHour > 23 ||
      InpFridayCloseHour < 0 || InpFridayCloseHour > 23 ||
      StringLen(InpStateId) < 1 || StringLen(InpStateId) > 8)
   {
      Print("Invalid EA inputs");
      return INIT_PARAMETERS_INCORRECT;
   }

   fastEmaHandle = iMA(_Symbol, InpSignalTimeframe, InpFastEmaPeriod,
                       0, MODE_EMA, PRICE_CLOSE);
   slowEmaHandle = iMA(_Symbol, InpSignalTimeframe, InpSlowEmaPeriod,
                       0, MODE_EMA, PRICE_CLOSE);
   atrHandle = iATR(_Symbol, InpSignalTimeframe, InpAtrPeriod);
   if(fastEmaHandle == INVALID_HANDLE ||
      slowEmaHandle == INVALID_HANDLE ||
      atrHandle == INVALID_HANDLE)
      return INIT_FAILED;

   if(InpUseHtf1Confluence)
   {
      htf1FastHandle = iMA(_Symbol, InpHtf1Timeframe, InpHtfFastEmaPeriod,
                           0, MODE_EMA, PRICE_CLOSE);
      htf1SlowHandle = iMA(_Symbol, InpHtf1Timeframe, InpHtfSlowEmaPeriod,
                           0, MODE_EMA, PRICE_CLOSE);
      if(htf1FastHandle == INVALID_HANDLE || htf1SlowHandle == INVALID_HANDLE)
         return INIT_FAILED;
   }
   if(InpUseHtf2Confluence)
   {
      htf2FastHandle = iMA(_Symbol, InpHtf2Timeframe, InpHtfFastEmaPeriod,
                           0, MODE_EMA, PRICE_CLOSE);
      htf2SlowHandle = iMA(_Symbol, InpHtf2Timeframe, InpHtfSlowEmaPeriod,
                           0, MODE_EMA, PRICE_CLOSE);
      if(htf2FastHandle == INVALID_HANDLE || htf2SlowHandle == INVALID_HANDLE)
         return INIT_FAILED;
   }
   if(InpMonitorEnabled)
   {
      m5RsiHandle  = iRSI(_Symbol, PERIOD_M5, InpMonitorRsiPeriod, PRICE_CLOSE);
      m15RsiHandle = iRSI(_Symbol, PERIOD_M15, InpMonitorRsiPeriod, PRICE_CLOSE);
      m5FastHandle  = iMA(_Symbol, PERIOD_M5, InpMonitorFastEma, 0, MODE_EMA, PRICE_CLOSE);
      m5SlowHandle  = iMA(_Symbol, PERIOD_M5, InpMonitorSlowEma, 0, MODE_EMA, PRICE_CLOSE);
      m15FastHandle = iMA(_Symbol, PERIOD_M15, InpMonitorFastEma, 0, MODE_EMA, PRICE_CLOSE);
      m15SlowHandle = iMA(_Symbol, PERIOD_M15, InpMonitorSlowEma, 0, MODE_EMA, PRICE_CLOSE);
      if(m5RsiHandle == INVALID_HANDLE || m15RsiHandle == INVALID_HANDLE ||
         m5FastHandle == INVALID_HANDLE || m5SlowHandle == INVALID_HANDLE ||
         m15FastHandle == INVALID_HANDLE || m15SlowHandle == INVALID_HANDLE)
         return INIT_FAILED;
   }

   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpSlippagePoints);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.SetAsyncMode(false);
   LoadState();
   EventSetTimer(1);

   Print("FTMOQuantEA initialized. Initial balance=", initialBalance,
         ", daily baseline=", dayStartBalance,
         ". Confirm reset time matches 00:00 CE(S)T.");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   if(fastEmaHandle != INVALID_HANDLE)
      IndicatorRelease(fastEmaHandle);
   if(slowEmaHandle != INVALID_HANDLE)
      IndicatorRelease(slowEmaHandle);
   if(atrHandle != INVALID_HANDLE)
      IndicatorRelease(atrHandle);
   if(htf1FastHandle != INVALID_HANDLE)
      IndicatorRelease(htf1FastHandle);
   if(htf1SlowHandle != INVALID_HANDLE)
      IndicatorRelease(htf1SlowHandle);
   if(htf2FastHandle != INVALID_HANDLE)
      IndicatorRelease(htf2FastHandle);
   if(htf2SlowHandle != INVALID_HANDLE)
      IndicatorRelease(htf2SlowHandle);
   if(m5RsiHandle != INVALID_HANDLE)
      IndicatorRelease(m5RsiHandle);
   if(m15RsiHandle != INVALID_HANDLE)
      IndicatorRelease(m15RsiHandle);
   if(m5FastHandle != INVALID_HANDLE)
      IndicatorRelease(m5FastHandle);
   if(m5SlowHandle != INVALID_HANDLE)
      IndicatorRelease(m5SlowHandle);
   if(m15FastHandle != INVALID_HANDLE)
      IndicatorRelease(m15FastHandle);
   if(m15SlowHandle != INVALID_HANDLE)
      IndicatorRelease(m15SlowHandle);
}

void OnTimer()
{
   // Account-level limits must still be watched when this chart is quiet.
   CheckAccountGuard();
   HandleWeekendExit();
}

void OnTick()
{
   bool accountSafe = CheckAccountGuard();
   HandleWeekendExit();
   ManagePositions();
   RunLowerTfMonitor();

   int units = CountUnits();
   if(units == 0)
   {
      SetTrendSide(0);
      SetPullback(false);
   }

   // Trend change: flatten every unit when the EMA stack flips against the
   // open trend. A fresh signal then starts the next trend.
   if(units > 0 && accountSafe)
   {
      double fast1 = IndicatorValue(fastEmaHandle, 1);
      double slow1 = IndicatorValue(slowEmaHandle, 1);
      int side = TrendSide();
      if(fast1 != EMPTY_VALUE && slow1 != EMPTY_VALUE &&
         ((side > 0 && fast1 < slow1) || (side < 0 && fast1 > slow1)))
      {
         CloseAllUnits("trend change");
         units = 0;
      }
   }

   if(!accountSafe || !IsTradingSession())
      return;

   int entries, losses;
   TodayStrategyStats(entries, losses);
   if(losses >= InpMaxLosingTradesPerDay)
      return;

   datetime bar = iTime(_Symbol, InpSignalTimeframe, 0);
   if(bar <= 0 || bar == lastSignalBar)
      return;
   lastSignalBar = bar;

   if(units > 0)
      UpdatePullback();

   if(units == 0)
   {
      if(entries >= InpMaxTradesPerDay)
         return;
      int signal = Signal();
      if(signal != 0 && OpenUnit(signal))
      {
         SetTrendSide(signal);
         ResetTrendExtreme();
         SetPullback(false);
      }
      return;
   }

   // Pyramid: after TP1 on the trend, add on the pullback resume.
   if(!InpPyramidEnabled || units >= InpMaxUnits ||
      !AnyUnitTp1Done() || !PullbackArmed())
      return;

   int side = TrendSide();
   double c1 = iClose(_Symbol, InpSignalTimeframe, 1);
   double h2 = iHigh(_Symbol, InpSignalTimeframe, 2);
   double l2 = iLow(_Symbol, InpSignalTimeframe, 2);
   double fast1 = IndicatorValue(fastEmaHandle, 1);
   double fast2 = IndicatorValue(fastEmaHandle, 2);
   double slow1 = IndicatorValue(slowEmaHandle, 1);
   if(fast1 == EMPTY_VALUE || fast2 == EMPTY_VALUE || slow1 == EMPTY_VALUE)
      return;
   bool resume = side > 0 ? c1 > h2 : c1 < l2;
   bool emaOk = side > 0 ? (fast1 > slow1 && fast1 > fast2)
                         : (fast1 < slow1 && fast1 < fast2);
   if(resume && emaOk && OpenUnit(side))
   {
      SetPullback(false);
      ResetTrendExtreme();
   }
}
