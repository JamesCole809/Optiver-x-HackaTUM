import sys
sys.path.append('.')
import time
import math
from collections import defaultdict, deque
from optibook.synchronous_client import Exchange
from config import (
    HOST, INFO_PORT, EXEC_PORT, USERNAME, PASSWORD,
    PRIMARY_STOCKS,
    MAX_POSITION_PER_INSTRUMENT,
    MAX_TOTAL_EXPOSURE,
    MAX_REQUESTS_PER_SECOND,
    BASE_POSITION_SIZE,
)

INTERNAL_POS_LIMIT = 90


class MidMMPrimaryBot:
    def __init__(self, exchange: Exchange):
        self.exchange = exchange
        self.start_time = time.time()
        self.per_instrument_limit = min(INTERNAL_POS_LIMIT, MAX_POSITION_PER_INSTRUMENT)
        self.positions = defaultdict(int)
        self.outstanding_orders = {
            inst: {"bid": None, "ask": None} for inst in PRIMARY_STOCKS
        }

        self.recent_requests = deque(maxlen=MAX_REQUESTS_PER_SECOND)
        self.tick_size = 0.1
        self.base_quote_volume = BASE_POSITION_SIZE

        # minimum half-spread (in ticks) around theo when the book is tight
        self.half_spread_ticks = 1      # 1 tick → 0.1 if tick_size=0.1

        self.inv_skew_coeff = 0.03
        self.inventory_soft_ratio = 0.8

        # when news hits an instrument, we widen its base half-spread to 3 ticks
        # for 15 seconds
        self.news_half_spread_ticks = 3
        self.news_window_seconds = 10.0
        self.news_widen_until = defaultdict(float)  # instrument -> timestamp

        # when global news hits (@GlobalMarkets or #GlobalEconomy),
        # we widen all spreads to 2 ticks for 15 seconds
        self.global_news_half_spread_ticks = 2
        self.global_news_window_seconds = 5.0
        self.global_news_widen_until = 0.0  # timestamp

        self.update_positions_from_exchange()

    def flag_news(self, instrument: str):
        """Call this when there is stock-specific news for `instrument`."""
        self.news_widen_until[instrument] = self._now() + self.news_window_seconds

    def flag_global_news(self):
        """Call this when you see @GlobalMarkets or #GlobalEconomy in the news."""
        self.global_news_widen_until = self._now() + self.global_news_window_seconds

    def cancel_all_existing_orders(self):
        for inst in PRIMARY_STOCKS:
            try:
                outstanding = self.exchange.get_outstanding_orders(inst)
                for order_id in list(outstanding.keys()):
                    try:
                        self.exchange.delete_order(inst, order_id)
                    except Exception:
                        pass
            except Exception:
                pass

    def _now(self):
        return time.time()

    def _elapsed(self):
        return self._now() - self.start_time

    def can_send_request(self) -> bool:
        now = self._now()
        while self.recent_requests and self.recent_requests[0] < now - 1.0:
            self.recent_requests.popleft()
        return len(self.recent_requests) < MAX_REQUESTS_PER_SECOND

    def record_request(self):
        self.recent_requests.append(self._now())

    def get_book(self, instrument: str):
        try:
            return self.exchange.get_last_price_book(instrument)
        except Exception:
            return None

    def update_positions_from_exchange(self):
        try:
            pos = self.exchange.get_positions()
            self.positions = defaultdict(int, pos)
        except Exception:
            pass

    def total_abs_exposure(self) -> int:
        return sum(abs(p) for p in self.positions.values())

    def within_limits(self, instrument: str, side: str, volume: int) -> bool:
        if volume <= 0:
            return False

        limit = self.per_instrument_limit

        try:
            pos_map = self.exchange.get_positions()
            self.positions = defaultdict(int, pos_map)
        except Exception:
            pass

        current_pos = self.positions.get(instrument, 0)

        try:
            outstanding = self.exchange.get_outstanding_orders(instrument)
        except Exception:
            outstanding = {}

        bid_out = 0
        ask_out = 0
        for _, ord_obj in outstanding.items():
            ord_side = getattr(ord_obj, "side", None)
            ord_vol = getattr(ord_obj, "volume", 0) or 0
            if ord_side == "bid":
                bid_out += ord_vol
            elif ord_side == "ask":
                ask_out += ord_vol

        total_out = bid_out + ask_out

        if side == "bid":
            worst_new_pos = current_pos + total_out + volume
        else:  # 'ask'
            worst_new_pos = current_pos - total_out - volume

        if abs(worst_new_pos) > limit:
            return False

        total_exposure_now = self.total_abs_exposure()
        worst_total_exposure = total_exposure_now + volume
        if worst_total_exposure > MAX_TOTAL_EXPOSURE:
            return False

        return True

    def compute_vwap_mid(self, book):
        if not book.bids or not book.asks:
            return None

        bid_notional = 0.0
        bid_volume = 0
        for level in book.bids:
            bid_notional += level.price * level.volume
            bid_volume += level.volume
        if bid_volume <= 0:
            return None
        vwap_bid = bid_notional / bid_volume

        ask_notional = 0.0
        ask_volume = 0
        for level in book.asks:
            ask_notional += level.price * level.volume
            ask_volume += level.volume
        if ask_volume <= 0:
            return None
        vwap_ask = ask_notional / ask_volume

        return (vwap_bid + vwap_ask) / 2.0

    def safe_insert_order(self, instrument: str, price: float, volume: int, side: str):
        if volume <= 0 or not self.can_send_request():
            return None

        try:
            response = self.exchange.insert_order(
                instrument_id=instrument,
                price=price,
                volume=volume,
                side=side,
                order_type="limit",
            )
            self.record_request()
            return getattr(response, "order_id", getattr(response, "orderId", None))
        except Exception:
            return None

    def safe_delete_order(self, instrument: str, order_id):
        if not order_id or not self.can_send_request():
            return

        try:
            self.exchange.delete_order(instrument_id=instrument, order_id=order_id)
            self.record_request()
        except Exception:
            pass

    def quote_instrument(self, instrument: str):
        book = self.get_book(instrument)
        if not book or not book.bids or not book.asks:
            oo = self.outstanding_orders[instrument]
            self.safe_delete_order(instrument, oo["bid"])
            self.safe_delete_order(instrument, oo["ask"])
            self.outstanding_orders[instrument] = {"bid": None, "ask": None}
            return

        best_bid = book.bids[0].price
        best_ask = book.asks[0].price

        mid = self.compute_vwap_mid(book)
        if mid is None:
            oo = self.outstanding_orders[instrument]
            self.safe_delete_order(instrument, oo["bid"])
            self.safe_delete_order(instrument, oo["ask"])
            self.outstanding_orders[instrument] = {"bid": None, "ask": None}
            return

        tick = self.tick_size
        inv = self.positions.get(instrument, 0)
        now = self._now()

        # hard position limit
        if abs(inv) >= self.per_instrument_limit:
            oo = self.outstanding_orders[instrument]
            self.safe_delete_order(instrument, oo["bid"])
            self.safe_delete_order(instrument, oo["ask"])
            self.outstanding_orders[instrument] = {"bid": None, "ask": None}
            return

        # inventory skew
        skew_ticks = self.inv_skew_coeff * inv
        fair = mid - skew_ticks * tick

        # start from normal base
        base_half_spread_ticks = self.half_spread_ticks

        # per-stock news: bump to at least 3 ticks for this instrument
        if now < self.news_widen_until.get(instrument, 0.0):
            base_half_spread_ticks = max(
                base_half_spread_ticks, self.news_half_spread_ticks
            )

        # global news: bump to at least 2 ticks for all instruments
        if now < self.global_news_widen_until:
            base_half_spread_ticks = max(
                base_half_spread_ticks, self.global_news_half_spread_ticks
            )

        base_half_spread = base_half_spread_ticks * tick

        # current top-of-book spread
        book_spread = max(best_ask - best_bid, tick)

        # if book is tight → use base_half_spread
        # if book is wide  → go wide as well (half_spread grows with book_spread)
        if book_spread <= 2 * base_half_spread:
            half_spread = base_half_spread
        else:
            half_spread = book_spread / 2.0

        # raw theo-based quotes
        bid_price = fair - half_spread
        ask_price = fair + half_spread

        # snap to tick
        bid_price = math.floor(bid_price / tick) * tick
        ask_price = math.ceil(ask_price / tick) * tick

        # stay passive vs venue top-of-book
        if bid_price >= best_ask:
            bid_price = best_ask - tick
        if ask_price <= best_bid:
            ask_price = best_bid + tick

        # final sanity
        if bid_price is None or ask_price is None or bid_price >= ask_price:
            oo = self.outstanding_orders[instrument]
            self.safe_delete_order(instrument, oo["bid"])
            self.safe_delete_order(instrument, oo["ask"])
            self.outstanding_orders[instrument] = {"bid": None, "ask": None}
            return

        # inventory-aware sizes
        base_vol = self.base_quote_volume
        if inv > 0:
            bid_volume = max(int(base_vol * 0.5), 0)
            ask_volume = max(int(base_vol * 1.5), 1)
        elif inv < 0:
            bid_volume = max(int(base_vol * 1.5), 1)
            ask_volume = max(int(base_vol * 0.5), 0)
        else:
            bid_volume = base_vol
            ask_volume = base_vol

        soft_limit = self.inventory_soft_ratio * self.per_instrument_limit
        if abs(inv) > soft_limit:
            if inv > 0:
                bid_volume = 0
            elif inv < 0:
                ask_volume = 0

        # anti self-trade guard
        if abs(ask_price - bid_price) < tick:
            if inv >= 0:
                bid_volume = 0
            else:
                ask_volume = 0

        # final sanity vs top of book
        if bid_volume > 0 and bid_price >= best_ask:
            bid_volume = 0
        if ask_volume > 0 and ask_price <= best_bid:
            ask_volume = 0

        bid_volume = max(bid_volume, 0)
        ask_volume = max(ask_volume, 0)

        # cancel old quotes
        oo = self.outstanding_orders[instrument]
        if oo["bid"] is not None:
            self.safe_delete_order(instrument, oo["bid"])
            oo["bid"] = None
        if oo["ask"] is not None:
            self.safe_delete_order(instrument, oo["ask"])
            oo["ask"] = None

        # new bid
        if bid_volume > 0 and self.within_limits(instrument, "bid", bid_volume):
            bid_id = self.safe_insert_order(
                instrument=instrument,
                price=bid_price,
                volume=bid_volume,
                side="bid",
            )
            oo["bid"] = bid_id

        # new ask
        if ask_volume > 0 and self.within_limits(instrument, "ask", ask_volume):
            ask_id = self.safe_insert_order(
                instrument=instrument,
                price=ask_price,
                volume=ask_volume,
                side="ask",
            )
            oo["ask"] = ask_id

        self.outstanding_orders[instrument] = oo

    def print_status(self):
        return

    def run(self, duration_seconds=None):
        self.cancel_all_existing_orders()

        last_status = self._now()
        last_pos_sync = self._now()
        last_quote_time = {inst: 0.0 for inst in PRIMARY_STOCKS}
        quote_interval = 1.5

        try:
            while True:
                if duration_seconds is not None and self._elapsed() > duration_seconds:
                    break

                now = self._now()

                if now - last_pos_sync > 5.0:
                    self.update_positions_from_exchange()
                    last_pos_sync = now

                for inst in PRIMARY_STOCKS:
                    if now - last_quote_time[inst] > quote_interval:
                        self.quote_instrument(inst)
                        last_quote_time[inst] = now

                if now - last_status > 30.0:
                    self.print_status()
                    last_status = now

                time.sleep(0.1)

        except KeyboardInterrupt:
            pass

        finally:
            for inst in PRIMARY_STOCKS:
                oo = self.outstanding_orders[inst]
                if oo["bid"] is not None:
                    self.safe_delete_order(inst, oo["bid"])
                if oo["ask"] is not None:
                    self.safe_delete_order(inst, oo["ask"])
            self.print_status()


def main():
    resp = input("Ready to connect?: ").strip().lower()
    if resp != "yes":
        return

    exchange = Exchange(
    )
    exchange.connect()

    bot = MidMMPrimaryBot(exchange)
    input("Press ENTER to start quoting...")
    bot.run()

    exchange.disconnect()


if __name__ == "__main__":
    main()
