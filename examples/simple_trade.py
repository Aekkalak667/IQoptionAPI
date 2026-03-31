import asyncio
import logging
import sys
import os

# Add src to path so we can import the library without installing
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from iq_option_nexus_api import NexusAPI

async def main():
    # Setup logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # Credentials (Replace with your own or use environment variables)
    email = os.getenv("IQ_EMAIL", "your_email@example.com")
    password = os.getenv("IQ_PASSWORD", "your_password")
    mode = "PRACTICE"

    api = NexusAPI(email, password, mode)
    
    print(f"Connecting to IQ Option as {email}...")
    if await api.connect():
        print("Connected successfully!")
        
        # Wait for login and profile
        while not api.is_logged_in:
            await asyncio.sleep(0.1)
            
        print(f"Logged in. Balance: {api.balance} {api.currency_symbol}")
        
        print("\nDiscovering assets...")
        await api.get_active_assets("binary")
        await api.get_active_assets("digital")
        
        # Wait for discovery
        await asyncio.sleep(5)
        
        print(f"\nDiscovered {len(api.assets_map)} assets.")
        
        # Show some discovered assets
        count = 0
        for active_id, info in api.assets_map.items():
            print(f"ID: {active_id} | Name: {info['name']} | Type: {info['type']} | Enabled: {info['is_enabled']}")
            count += 1
            if count >= 10: break
            
        print("\nExample finished.")
    else:
        print("Failed to connect. Check your credentials.")

if __name__ == "__main__":
    asyncio.run(main())
