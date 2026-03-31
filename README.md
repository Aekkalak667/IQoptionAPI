<!-- Source: src/iq_option_nexus_api/core.py, examples/simple_trade.py -->
# IQ Option Nexus API SDK

Lightweight asynchronous wrapper for IQ Option WebSocket API.

## Features

- **Async/Await Support**: Built on top of `websockets` and `asyncio` for high performance.
- **Binary & Digital Options**: Support for both binary and digital option trading.
- **Real-time Candle Stream**: Subscribe to live market data with ease.
- **Heartbeat System**: Built-in heartbeat to maintain connection stability.
- **Auto-reconnect**: Robust connection management.
- **Asset Discovery**: Automatic discovery of available assets and their IDs.

## Installation

Install the SDK using pip:

```bash
pip install git+https://github.com/Aekkalak/iq-option-nexus-api.git
```

## Quick Start

The following example demonstrates how to connect to the API and discover available assets.

```python
import asyncio
from iq_option_nexus_api import NexusAPI

async def main():
    # Credentials
    email = "your_email@example.com"
    password = "your_password"
    
    api = NexusAPI(email, password, mode="PRACTICE")
    
    print(f"Connecting to IQ Option...")
    if await api.connect():
        # Wait for login and profile
        while not api.is_logged_in:
            await asyncio.sleep(0.1)
            
        print(f"Logged in. Balance: {api.balance} {api.currency_symbol}")
        
        # Discover assets
        await api.get_active_assets("binary")
        await api.get_active_assets("digital")
        
        # Wait for discovery to complete
        await asyncio.sleep(5)
        print(f"Discovered {len(api.assets_map)} assets.")
    else:
        print("Failed to connect. Check your credentials.")

if __name__ == "__main__":
    asyncio.run(main())
```

## Disclaimer

**Financial Risk Warning:** Trading binary and digital options involves significant risk and can result in the loss of your invested capital. This SDK is provided for educational and research purposes only. The authors are not responsible for any financial losses incurred through the use of this software. Always trade responsibly and within your means.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
