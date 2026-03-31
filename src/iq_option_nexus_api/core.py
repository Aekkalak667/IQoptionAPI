import asyncio
import websockets
import json
import logging
import time
import ssl
import requests
import os
from datetime import datetime, timezone, timedelta
from collections import deque
from .constants import ASSETS_BINARY, ASSETS_DIGITAL

class NexusAPI:
    """
    Nexus API SDK v1.1: Precision Sync Master.
    """
    def __init__(self, email, password, mode="PRACTICE"):
        self.url = "wss://iqoption.com/echo/websocket"
        self.email = email
        self.password = password
        self.mode = mode.upper()
        
        self.ws = None
        self.ssid = None
        self.is_connected = False
        self.is_logged_in = False
        self.balance_id = None
        self.balance = 0.0
        self.currency_symbol = "$"
        
        # Time Sync
        self.server_time = 0
        self.time_delta = 0
        
        self.candles = {} 
        self.results = {} 
        self.digital_instruments = {} 
        self.pending_requests = {}
        self.candle_futures = {} # For historical data requests
        self.order_to_req = {} # Map order_id to request_id
        self.assets_map = {}
        self.name_to_id = {} # Reverse lookup map for O(1) performance
        
        self._asset_map_binary = {
            "EURUSD": 1, "GBPUSD": 2, "GBPJPY": 3, "EURJPY": 4, "EURGBP": 5,
            "AUDUSD": 7, "USDCAD": 8, "USDJPY": 6, "NZDUSD": 84
        }
        
        self.logger = logging.getLogger("NexusAPI")
        self.cache_path = "assets_cache.json"
        self.load_assets_cache()

    async def save_assets_cache(self):
        """Saves assets map to cache file asynchronously."""
        def _write():
            with open(self.cache_path, "w") as f:
                json.dump(self.assets_map, f, indent=4)
        
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _write)
            self.logger.info(f"Assets cache saved to {self.cache_path}")
        except Exception as e:
            self.logger.error(f"Failed to save assets cache: {e}")

    def load_assets_cache(self):
        try:
            if os.path.exists(self.cache_path):
                with open(self.cache_path, "r") as f:
                    cached_data = json.load(f)
                    # Convert keys back to int as JSON keys are always strings
                    self.assets_map = {int(k): v for k, v in cached_data.items()}
                    # Populate reverse lookup map
                    for active_id, info in self.assets_map.items():
                        self.name_to_id[(info["name"], info["type"])] = active_id
                self.logger.info(f"Loaded {len(self.assets_map)} assets from cache.")
        except Exception as e:
            self.logger.error(f"Failed to load assets cache: {e}")

    def get_ssid(self):
        session = requests.Session()
        try:
            r = session.post("https://auth.iqoption.com/api/v2/login", 
                             json={"identifier": self.email, "password": self.password},
                             headers={"User-Agent": "Mozilla/5.0"})
            
            self.logger.info(f"Login attempt for {self.email} - Status: {r.status_code}")
            
            if r.status_code == 200:
                response_data = r.json()
                self.ssid = r.cookies.get("ssid") or response_data.get("ssid")
                if self.ssid:
                    self.logger.info("Login successful, SSID obtained.")
                    return True
                else:
                    self.logger.error(f"Login successful but no SSID found in response: {response_data}")
            else:
                self.logger.error(f"Login failed - Status: {r.status_code}, Response: {r.text}")
        except Exception as e:
            self.logger.error(f"Login request error: {e}")
            
        return False

    async def connect(self):
        if not self.ssid and not self.get_ssid(): 
            self.logger.error("Cannot connect: SSID missing and login failed.")
            return False
            
        try:
            try:
                # Try standard connection first (Secure)
                self.ws = await websockets.connect(self.url)
                self.logger.info("Connected to IQ Option (Secure SSL)")
            except (ssl.SSLCertVerificationError, ssl.SSLError) as ssl_err:
                self.logger.warning(f"SSL Verification failed: {ssl_err}. Retrying with unverified context...")
                ssl_context = ssl._create_unverified_context()
                self.ws = await websockets.connect(self.url, ssl=ssl_context)
                self.logger.info("Connected to IQ Option (Unverified SSL Fallback)")
            
            if self.ws:
                self.is_connected = True
                asyncio.create_task(self._receiver())
                asyncio.create_task(self._heartbeat())
                await self._send("ssid", self.ssid)
                return True
        except Exception as e:
            self.logger.error(f"Connection failed: {e}")
            self.is_connected = False
            
        return False

    async def get_active_assets(self, asset_type="binary"):
        """Fetches active assets from the server."""
        if not self.ws: return
        if asset_type == "binary":
            # Try top-level get-actives
            await self._send("get-actives", {"types": [1, 2]})
        elif asset_type == "digital":
            # Fix 4300: Send get-instruments directly instead of wrapping in sendMessage
            await self._send("get-instruments", {"type": "digital-option"}, version="1.0")
            # Also try get-actives with type 3 (Digital)
            await self._send("get-actives", {"types": [3]})
        else:
            return

    async def _send(self, name, msg, version="1.0"):
        if not self.ws: return None
        request_id = str(int(time.time() * 1000))
        payload = {"name": name, "msg": msg, "version": version, "request_id": request_id}
        self.logger.debug(f"Sending {name}: {payload}")
        await self.ws.send(json.dumps(payload))
        return request_id

    async def _heartbeat(self):
        while self.is_connected:
            try:
                await self.ws.send("2")
                await asyncio.sleep(5)
            except: 
                self.is_connected = False
                break

    def _get_active_id(self, asset_name, asset_type="binary"):
        """
        Finds the active_id for a given asset name and type.
        Uses O(1) lookup with fallback to Smart Name Matching.
        """
        # 1. O(1) Lookup in name_to_id
        active_id = self.name_to_id.get((asset_name, asset_type))
        if active_id:
            return active_id
        
        # 2. Smart Name Matching: Try OTC version if not OTC, or vice versa
        alt_name = asset_name + "-OTC" if "-OTC" not in asset_name else asset_name.replace("-OTC", "")
        active_id = self.name_to_id.get((alt_name, asset_type))
        if active_id:
            self.logger.warning(f"Asset {asset_name} not found, using {alt_name} (ID: {active_id})")
            return active_id

        # 3. Fallback to constants
        if asset_type == "digital":
            active_id = ASSETS_DIGITAL.get(asset_name)
            if not active_id:
                base_name = asset_name.replace("-OTC", "")
                active_id = ASSETS_DIGITAL.get(base_name)
        else:
            active_id = ASSETS_BINARY.get(asset_name)
            if not active_id:
                base_name = asset_name.replace("-OTC", "")
                active_id = ASSETS_BINARY.get(base_name)
        
        # Return None if not found to prevent opening wrong asset (Safety First)
        if not active_id:
            self.logger.error(f"Asset {asset_name} ({asset_type}) not found in discovery or constants.")
            return None
            
        return active_id

    async def get_candles(self, asset, timeframe, count, to_time=None):
        """
        Requests historical candles for a given asset.
        """
        active_id = self._get_active_id(asset)
        if not active_id:
            return None
            
        if to_time is None:
            to_time = int(self.server_time if self.server_time > 0 else time.time())
            
        # Calculate 'from' to satisfy the prompt's structure
        from_time = to_time - (count * timeframe)
        
        msg = {
            "name": "get-candles",
            "version": "2.0",
            "body": {
                "active_id": active_id,
                "size": timeframe,
                "from": from_time,
                "to": to_time,
                "count": count
            }
        }
        
        future = asyncio.get_event_loop().create_future()
        request_id = await self._send("sendMessage", msg)
        
        if not request_id:
            return None
            
        self.candle_futures[request_id] = (asset, future)
        
        try:
            return await asyncio.wait_for(future, timeout=10.0)
        except asyncio.TimeoutError:
            self.logger.error(f"Timeout waiting for candles for {asset}")
            if request_id in self.candle_futures:
                del self.candle_futures[request_id]
            return None

    async def subscribe(self, asset, timeframe=300, asset_type="binary"):
        """Subscribes to market data. asset_type can be 'binary' or 'digital'"""
        active_id = self._get_active_id(asset, asset_type)
        if not active_id: return
            
        if asset not in self.candles: self.candles[asset] = deque(maxlen=300)
        
        # We subscribe using the requested active_id
        await self._send("subscribeMessage", {
            "name": "candle-generated", 
            "params": {"routingFilters": {"active_id": active_id, "size": timeframe}}
        })
        self.logger.info(f"Subscribed to {asset} as {asset_type} (ID: {active_id})")

    async def subscribe_instruments(self, asset):
        """Subscribes to digital option instruments for a given asset."""
        # Use string format for subscribeMessage as it's the standard for instrument sniffing
        await self._send("subscribeMessage", f"digital-option-instruments.{asset}")
        self.logger.info(f"Subscribed to digital-option-instruments.{asset}")

    async def buy(self, asset, amount, direction, duration=1):
        if not self.balance_id: return False, None
        
        # Use discovered ID for digital options
        d_id = self._get_active_id(asset, "digital")
        if not d_id: return False, None
        
        side_letter = "C" if direction.lower() in ["buy", "call"] else "P"
        
        # Perfect Expiration Algorithm (RESTORED WORKING FORMAT)
        # Use server_time if available, fallback to local time
        base_time = self.server_time if self.server_time > 0 else time.time()
        now_utc = datetime.fromtimestamp(base_time, tz=timezone.utc)
        
        # Round to the next 'duration' interval (e.g., for M5: :00, :05, :10)
        expire_minute = (now_utc.minute // duration + 1) * duration
        exp_time = now_utc.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=expire_minute)
        
        # Safety: If less than 30 seconds remaining, move to the next interval
        if (exp_time - now_utc).total_seconds() < 30:
            exp_time += timedelta(minutes=duration)
            
        exp_str = exp_time.strftime('%Y%m%dD%H%M00')
        inst_id = f"do{d_id}A{exp_str}T{duration}M{side_letter}SPT"
        
        msg = {
            "name": "digital-options.place-digital-option",
            "version": "2.0",
            "body": {
                "user_balance_id": self.balance_id, "asset_id": d_id, 
                "instrument_id": inst_id, "instrument_index": 0,
                "amount": str(int(amount)), "side": "buy"
            }
        }
        self.logger.info(f"PLACING DIGITAL: {inst_id} Amount: {amount}")
        req_id = await self._send("sendMessage", msg)
        if req_id:
            self.pending_requests[req_id] = asset
            return True, req_id
        return False, None

    async def buy_binary(self, asset, amount, direction, duration=1):
        if not self.balance_id: return False, None
        
        active_id = self._get_active_id(asset, "binary")
        if not active_id: return False, None
        
        side = "call" if direction.lower() in ["buy", "call"] else "put"
        
        # Binary expiration calculation
        base_time = self.server_time if self.server_time > 0 else time.time()
        # Binary options usually expire at the end of the minute
        exp_timestamp = int(base_time / 60) * 60 + (duration * 60)
        
        # Structure as requested: {"name": "binary-options.place-binary-option", "body": {...}}
        msg = {
            "name": "binary-options.place-binary-option",
            "body": {
                "user_balance_id": self.balance_id,
                "active_id": active_id,
                "amount": str(int(amount)),
                "direction": side,
                "expired": exp_timestamp
            }
        }
        
        self.logger.info(f"PLACING BINARY: {asset} (ID: {active_id}) Amount: {amount} Exp: {exp_timestamp}")
        # We use sendMessage to wrap the requested structure
        req_id = await self._send("sendMessage", msg)
        if req_id:
            self.pending_requests[req_id] = asset
            return True, req_id
        return False, None

    def _cleanup_results(self):
        """Removes results older than 1 hour to prevent memory bloat."""
        now = time.time()
        one_hour_ago = now - 3600
        to_delete = [oid for oid, res in self.results.items() if res.get("time", 0) < one_hour_ago]
        for oid in to_delete:
            del self.results[oid]

    async def _receiver(self):
        try:
            async for message in self.ws:
                if message in ["2", "3"]: continue
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    self.logger.error(f"Failed to decode message: {message}")
                    continue
                    
                name, msg = data.get("name"), data.get("msg")
                if name != "timeSync":
                    # Enhanced debug for order execution verification
                    if name in ["digital-option-placed", "option-placed", "digital-option-closed", "option-closed", "error"]:
                        self.logger.info(f"CRITICAL RESPONSE: {name} -> {json.dumps(data)}")
                    else:
                        self.logger.debug(f"Received {name}: {data}")
                
                if name == "timeSync":
                    self.server_time = msg / 1000
                    self.time_delta = self.server_time - time.time()

                elif name == "digital-option-instruments":
                    # Instrument Sniffer: Print first 5 instrument IDs
                    instruments = msg.get("instruments", [])
                    self.logger.info(f"SNIFFER: Found {len(instruments)} instruments for {msg.get('active_id')}")
                    for i, inst in enumerate(instruments[:5]):
                        self.logger.info(f"SNIFFER [{i}]: {inst.get('id')}")

                elif name == "profile":
                    self.currency_symbol = msg.get("currency_char", "$")
                    for b in msg.get("balances", []):
                        # Match balance based on mode
                        if (self.mode == "PRACTICE" and b["type"] == 4) or (self.mode == "REAL" and b["type"] == 1):
                            self.balance_id, self.balance = b["id"], b["amount"]
                            if "currency_char" in b:
                                self.currency_symbol = b["currency_char"]
                    self.is_logged_in = True

                elif name == "balance-changed":
                    if msg.get("id") == self.balance_id:
                        self.balance = msg.get("current_balance", {}).get("amount", self.balance)
                        self.logger.info(f"Balance updated: {self.balance}")

                elif name in ["digital-option-placed", "option-placed"]:
                    req_id = data.get("request_id")
                    order_id = msg.get("id")
                    if req_id and order_id:
                        self.order_to_req[order_id] = req_id

                elif name == "digital-option-closed":
                    order_id = msg.get("id")
                    profit = msg.get("profit_amount", 0)
                    asset_id = msg.get("asset_id")
                    asset_info = self.assets_map.get(asset_id)
                    asset_name = asset_info["name"] if asset_info else f"ID_{asset_id}"
                    
                    status = "win" if profit > 0 else "loss"
                    req_id = self.order_to_req.pop(order_id, order_id)
                    self.results[req_id] = {
                        "status": status,
                        "profit": profit,
                        "asset": asset_name,
                        "time": time.time()
                    }
                    self.logger.info(f"Digital Order {order_id} closed: {status} (Profit: {profit})")
                    self._cleanup_results()

                elif name == "option-closed":
                    order_id = msg.get("id")
                    # Binary profit calculation: win_amount is total return, amount is stake
                    amount = msg.get("amount", 0)
                    win_amount = msg.get("win_amount", 0)
                    profit = win_amount - amount
                    
                    win_status = msg.get("win")
                    status = "win" if win_status == "win" else "loss"
                    asset_id = msg.get("active_id")
                    asset_info = self.assets_map.get(asset_id)
                    asset_name = asset_info["name"] if asset_info else f"ID_{asset_id}"
                    
                    req_id = self.order_to_req.pop(order_id, order_id)
                    self.results[req_id] = {
                        "status": status,
                        "profit": profit,
                        "asset": asset_name,
                        "time": time.time()
                    }
                    self.logger.info(f"Binary Order {order_id} closed: {status} (Profit: {profit})")
                    self._cleanup_results()

                elif name == "actives":
                    if isinstance(msg, list):
                        for asset in msg:
                            active_id = asset.get("id")
                            name_val = asset.get("name", "")
                            self.assets_map[active_id] = {
                                "name": name_val,
                                "type": "binary",
                                "is_enabled": asset.get("is_enabled", False),
                                "is_otc": "-OTC" in name_val
                            }
                            self.name_to_id[(name_val, "binary")] = active_id
                        self.logger.info(f"Discovered {len(msg)} binary assets.")
                        asyncio.create_task(self.save_assets_cache())

                elif name == "instruments":
                    if isinstance(msg, dict) and "instruments" in msg:
                        inst_list = msg.get("instruments", [])
                        for inst in inst_list:
                            active_id = inst.get("active_id")
                            name_val = inst.get("name", "") or inst.get("id", "")
                            self.assets_map[active_id] = {
                                "name": name_val,
                                "type": "digital",
                                "is_enabled": inst.get("is_active", False),
                                "is_otc": "-OTC" in name_val
                            }
                            self.name_to_id[(name_val, "digital")] = active_id
                        self.logger.info(f"Discovered {len(inst_list)} digital assets.")
                        asyncio.create_task(self.save_assets_cache())

                elif name == "candles":
                    req_id = data.get("request_id")
                    if req_id in self.candle_futures:
                        asset_name, future = self.candle_futures.pop(req_id)
                        candles_data = msg.get("candles", [])
                        
                        self.logger.info(f"Received {len(candles_data)} candles for {asset_name}")
                        
                        if asset_name not in self.candles:
                            self.candles[asset_name] = deque(maxlen=300)
                        
                        # Sort to ensure order
                        candles_data.sort(key=lambda x: x['at'])
                        
                        # Update the real-time buffer with the latest data from this batch
                        for candle in candles_data:
                            if not self.candles[asset_name] or candle['at'] > self.candles[asset_name][-1]['at']:
                                self.candles[asset_name].append(candle)
                            elif candle['at'] == self.candles[asset_name][-1]['at']:
                                self.candles[asset_name][-1] = candle
                        
                        if not future.done():
                            # Return the full batch of candles received
                            future.set_result(candles_data)
                
                elif name == "candle-generated":
                    asset_id = msg.get("active_id")
                    # Smart Lookup: Check assets_map first
                    asset_info = self.assets_map.get(asset_id)
                    asset_name = asset_info["name"] if asset_info else None
                    
                    if not asset_name:
                        # Fallback to constants
                        asset_name = next((k for k, v in ASSETS_BINARY.items() if v == asset_id), None)
                        if not asset_name:
                            asset_name = next((k for k, v in ASSETS_DIGITAL.items() if v == asset_id), "Unknown")
                    
                    if asset_name in self.candles or asset_name == "Unknown":
                        target_key = asset_name if asset_name != "Unknown" else f"ID_{asset_id}"
                        if target_key not in self.candles: self.candles[target_key] = deque(maxlen=300)
                        
                        if self.candles[target_key] and self.candles[target_key][-1]['at'] == msg['at']:
                            self.candles[target_key][-1] = msg
                        else: self.candles[target_key].append(msg)
        except Exception as e:
            self.logger.error(f"Receiver error: {e}")
        finally:
            self.is_connected = False
            self.is_logged_in = False
            self.logger.info("Connection closed, state reset.")
