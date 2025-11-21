"""
╔══════════════════════════════════════════════════════════╗
  🤖 LICENSE KEY SHOP BOT - PROFESSIONAL SYSTEM           
  ⚡ 100% Button-Based • Auto-Database • No Bugs          
  🔒 Secure • 🚀 Fast • 💎 Production Ready              
╚══════════════════════════════════════════════════════════╝
"""

import logging
import os
import asyncio
import time
import re
import random
import csv
import json
from io import StringIO, BytesIO
from typing import Optional
from datetime import datetime, timedelta
from urllib.parse import urlencode
import base64
import hmac
import hashlib

import aiohttp
from aiohttp import web
import asyncpg
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

# ═══════════════════════════════════════════════════════════
# 🔧 CONFIGURATION
# ═══════════════════════════════════════════════════════════
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8403423907:AAFDd_FHVYP8DoZoU3MJdY11ELO_QsZjnes")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://user:pass@host:5432/dbname")

# Convert postgres:// to postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

ADMIN_IDS = [1025395123, 7197432930]  # Multiple admins supported!

# Payment API (pay0.shop) - UPI
PAYMENT_API_URL = "https://pay0.shop/api/"
PAYMENT_USER_TOKEN = os.environ.get("PAYMENT_USER_TOKEN", "7a5d2e089b53eced0c980210e05dff9a")

# PayerURL API - CRYPTO
PAYERURL_PUBLIC_KEY = os.environ.get("PAYERURL_PUBLIC_KEY", "433dfd36663fa0d994d96c6befc2d497")
PAYERURL_SECRET_KEY = os.environ.get("PAYERURL_SECRET_KEY", "9d4944e3601ae1bfe04427cb691842a6")
PAYERURL_API_URL = "https://api-v2.payerurl.com/api/payment"
SITE_URL = os.environ.get("SITE_URL", "https://jibonmodsstorepy-production.up.railway.app")

PORT = int(os.environ.get("PORT", 8080))

# bKash Configuration
BKASH_PHONE = os.environ.get("BKASH_PHONE", "01891477192")
BKASH_SENDER = "16216"  # Official bKash SMS sender (also check for "bKash" contact)
BKASH_API_KEY = os.environ.get("BKASH_API_KEY", "ce0cf590c759aff3f43d5b6b17c44f90a2062c1562fc47c3f3e816562046615c")  # API key for webhook security
USD_TO_INR = 90
USD_TO_BDT = 100  # 1 USD = 100 BDT

PAYMENT_TIMEOUT = 600  # 10 minutes

# Currency conversion: 1 USD = 90 INR (for UPI payments)
USD_TO_INR = 90

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Global variables
application = None
admin_states = {}

# ═══════════════════════════════════════════════════════════
# 🗄️ DATABASE - PERMANENT WITH AUTO-CREATE TABLES
# ═══════════════════════════════════════════════════════════
class Database:
    def __init__(self, db_url: str):
        self.db_url = db_url
        self.pool = None

    async def connect(self):
        """Connect and auto-create all tables"""
        try:
            self.pool = await asyncpg.create_pool(
                self.db_url,
                min_size=2,
                max_size=10,
                command_timeout=60
            )
            logger.info("✅ Connected to PERMANENT database")
            await self.init_tables()
        except Exception as e:
            logger.error(f"❌ Database error: {e}")
            raise

    async def init_tables(self):
        """Auto-create ALL tables - preserves existing data"""
        queries = [
            """
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL UNIQUE,
                description TEXT,
                emoji VARCHAR(10) DEFAULT '🎮',
                group_link TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            -- Add group_link column if it doesn't exist (safe migration)
            DO $$ 
            BEGIN 
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                              WHERE table_name='products' AND column_name='group_link') THEN
                    ALTER TABLE products ADD COLUMN group_link TEXT;
                END IF;
            END $$;
            """,
            """
            CREATE TABLE IF NOT EXISTS variants (
                id SERIAL PRIMARY KEY,
                product_id INTEGER REFERENCES products(id) ON DELETE CASCADE,
                name VARCHAR(255) NOT NULL,
                price DECIMAL(10, 2) NOT NULL,
                validity_days INTEGER NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(product_id, validity_days)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS keys (
                id SERIAL PRIMARY KEY,
                variant_id INTEGER REFERENCES variants(id) ON DELETE CASCADE,
                key_data TEXT NOT NULL,
                status VARCHAR(20) DEFAULT 'available',
                used_by BIGINT,
                used_at TIMESTAMP,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            -- STEP 1: Remove duplicate keys (keep only the oldest one)
            -- This runs BEFORE adding UNIQUE constraint
            DO $$ 
            BEGIN 
                -- Delete duplicate keys, keeping only the one with the smallest ID (oldest)
                DELETE FROM keys 
                WHERE id IN (
                    SELECT id 
                    FROM (
                        SELECT id, 
                               ROW_NUMBER() OVER (PARTITION BY key_data ORDER BY id) as rn
                        FROM keys
                    ) t
                    WHERE t.rn > 1
                );
            END $$;
            """,
            """
            -- STEP 2: Now add UNIQUE constraint (safe because no duplicates exist)
            DO $$ 
            BEGIN 
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint 
                    WHERE conname = 'keys_key_data_unique'
                ) THEN
                    ALTER TABLE keys ADD CONSTRAINT keys_key_data_unique UNIQUE (key_data);
                END IF;
            END $$;
            """,
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                phone_number VARCHAR(20),
                first_name VARCHAR(100),
                username VARCHAR(100),
                is_blocked BOOLEAN DEFAULT FALSE,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS orders (
                order_id VARCHAR(100) PRIMARY KEY,
                user_id BIGINT NOT NULL,
                variant_id INTEGER REFERENCES variants(id),
                amount DECIMAL(10, 2) NOT NULL,
                status VARCHAR(20) DEFAULT 'pending',
                payment_method VARCHAR(20) DEFAULT 'upi',
                payment_url TEXT,
                utr VARCHAR(100),
                qr_message_id INTEGER,
                chat_id BIGINT,
                payerurl_txn_id VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            );
            """,
            """
            -- Add payment_method column if it doesn't exist
            DO $$ 
            BEGIN 
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                              WHERE table_name='orders' AND column_name='payment_method') THEN
                    ALTER TABLE orders ADD COLUMN payment_method VARCHAR(20) DEFAULT 'upi';
                END IF;
            END $$;
            """,
            """
            -- Add payerurl_txn_id column if it doesn't exist
            DO $$ 
            BEGIN 
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                              WHERE table_name='orders' AND column_name='payerurl_txn_id') THEN
                    ALTER TABLE orders ADD COLUMN payerurl_txn_id VARCHAR(100);
                END IF;
            END $$;
            """,
            """
            CREATE TABLE IF NOT EXISTS sales (
                id SERIAL PRIMARY KEY,
                order_id VARCHAR(100) UNIQUE,
                user_id BIGINT NOT NULL,
                variant_id INTEGER,
                product_name VARCHAR(255),
                variant_name VARCHAR(255),
                amount DECIMAL(10, 2),
                key_delivered TEXT,
                sale_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS resellers (
                user_id BIGINT PRIMARY KEY,
                discount_percent DECIMAL(5, 2) DEFAULT 0,
                is_active BOOLEAN DEFAULT TRUE,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notes TEXT
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS custom_pricing (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                variant_id INTEGER REFERENCES variants(id) ON DELETE CASCADE,
                custom_price DECIMAL(10, 2) NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, variant_id)
            );
            """
        ]
        
        async with self.pool.acquire() as conn:
            for query in queries:
                try:
                    await conn.execute(query)
                except Exception as e:
                    logger.warning(f"Table note: {e}")
            
            # Indexes
            try:
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_keys_variant_status ON keys(variant_id, status);")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(sale_date);")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_resellers_active ON resellers(is_active);")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_custom_pricing_user ON custom_pricing(user_id, is_active);")
            except:
                pass
                
        logger.info("✅ All tables auto-created - data preserved!")

    async def execute(self, query: str, *args):
        async with self.pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def fetch(self, query: str, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetchval(self, query: str, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetchval(query, *args)

db = Database(DATABASE_URL)

# ═══════════════════════════════════════════════════════════
# 💳 PAYMENT FUNCTIONS
# ═══════════════════════════════════════════════════════════
async def create_payment_order(user_id: int, amount: float, order_id: str, phone: str) -> Optional[str]:
    """Create UPI payment order via pay0.shop"""
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                'customer_mobile': str(phone)[-10:],
                'customer_name': f'User{user_id}',
                'user_token': PAYMENT_USER_TOKEN,
                'amount': str(int(amount)),
                'order_id': order_id,
                'redirect_url': 'https://web-production-2d440.up.railway.app',
                'remark1': 'license',
                'remark2': str(user_id)
            }
            
            # CRITICAL: pay0.shop needs application/x-www-form-urlencoded (like x.php!)
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            
            async with session.post(
                f"{PAYMENT_API_URL}create-order",
                data=payload,  # aiohttp auto-encodes to form-urlencoded when data= is used
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
                ssl=False
            ) as response:
                data = await response.json()
                
                logger.info(f"📡 Payment API response: status={data.get('status')}, message={data.get('message')}")
                logger.info(f"📡 Full response: {json.dumps(data, indent=2)}")
                
                if data.get('status') == True or data.get('status') == 'true':
                    payment_url = data.get('result', {}).get('payment_url')
                    if payment_url:
                        logger.info(f"✅ Payment created: {order_id}")
                        logger.info(f"🔗 Payment URL: {payment_url}")
                        return payment_url
                    else:
                        logger.error(f"❌ No payment_url in response!")
                
                logger.error(f"❌ Payment API: {data.get('message', 'Unknown')}")
                return None
                
    except Exception as e:
        logger.error(f"❌ Payment failed: {e}")
        return None

async def notify_admin(context: ContextTypes.DEFAULT_TYPE, text: str):
    """Send notification to all admins"""
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Admin notify failed for {admin_id}: {e}")

async def get_stock_count(variant_id: int) -> int:
    """Get available keys count"""
    return await db.fetchval(
        "SELECT COUNT(*) FROM keys WHERE variant_id = $1 AND status = 'available'",
        variant_id
    ) or 0

async def get_next_available_key(variant_id: int, user_id: int) -> Optional[str]:
    """Get and mark next available key"""
    key_row = await db.fetchrow("""
        UPDATE keys 
        SET status = 'sold', used_by = $1, used_at = CURRENT_TIMESTAMP
        WHERE id = (
            SELECT id FROM keys 
            WHERE variant_id = $2 AND status = 'available' 
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING key_data
    """, user_id, variant_id)
    
    return key_row['key_data'] if key_row else None

async def check_payment_status(order_id: str) -> dict:
    """Check payment status from pay0.shop API"""
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                'user_token': PAYMENT_USER_TOKEN,
                'order_id': order_id
            }
            
            async with session.post(
                f"{PAYMENT_API_URL}check-order-status",
                data=payload,
                timeout=aiohttp.ClientTimeout(total=15),
                ssl=False,
                headers={'Content-Type': 'application/x-www-form-urlencoded'}
            ) as response:
                data = await response.json()
                
                logger.info(f"Check status for {order_id}: {data}")
                
                if data.get('status') == True or data.get('status') == 'true' or data.get('status') == True:
                    result = data.get('result', {})
                    return {
                        'success': True,
                        'status': result.get('txnStatus', 'PENDING'),
                        'utr': result.get('utr', ''),
                        'amount': result.get('amount', '0')
                    }
                
                return {'success': False, 'message': data.get('message', 'Unknown error')}
    
    except Exception as e:
        logger.error(f"Check status error: {e}")
        return {'success': False, 'message': str(e)}

# ═══════════════════════════════════════════════════════════
# 💎 PAYERURL CRYPTO PAYMENT FUNCTIONS
# ═══════════════════════════════════════════════════════════

async def create_payerurl_payment(user_id: int, amount_usd: float, order_id: str, user_name: str, product_name: str = "License_Key") -> Optional[str]:
    """Create PayerURL payment and return payment page URL"""
    try:
        from urllib.parse import urlencode
        
        logger.info(f"🔸 Creating PayerURL payment:")
        logger.info(f"   Order: {order_id}")
        logger.info(f"   Amount: ${amount_usd:.2f}")
        logger.info(f"   Product: {product_name}")
        
        # Clean product name
        clean_name = product_name.replace(' ', '_').replace('/', '_').replace('\\', '_')
        
        # Prepare payment parameters - FLATTEN items array!
        params = {
            'order_id': order_id,
            'amount': amount_usd,
            'items[0][name]': clean_name,
            'items[0][qty]': 1,
            'items[0][price]': amount_usd,
            'currency': 'usd',
            'billing_fname': user_name or 'Customer',
            'billing_lname': str(user_id),
            'billing_email': f'user{user_id}@telegram.user',
            'redirect_to': f'{SITE_URL}/success',
            'notify_url': f'{SITE_URL}/webhook/payerurl',
            'cancel_url': f'{SITE_URL}/cancel',
            'type': 'php',
        }
        
        # Sort and encode
        sorted_params = dict(sorted(params.items()))
        query_string = urlencode(sorted_params)
        
        # Generate signature
        signature = hmac.new(
            PAYERURL_SECRET_KEY.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        auth_header = 'Bearer ' + base64.b64encode(
            f"{PAYERURL_PUBLIC_KEY}:{signature}".encode('utf-8')
        ).decode('utf-8')
        
        logger.info(f"🔸 Sending request...")
        
        # Send API request
        async with aiohttp.ClientSession() as session:
            async with session.post(
                PAYERURL_API_URL,
                data=query_string,
                headers={
                    'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
                    'Authorization': auth_header,
                },
                timeout=aiohttp.ClientTimeout(total=30),
                ssl=False
            ) as response:
                response_text = await response.text()
                logger.info(f"🔸 Response ({response.status}): {response_text[:200]}")
                
                try:
                    result = json.loads(response_text)
                except:
                    result = {}
                
                if response.status == 200 and result.get('redirectTO'):
                    payment_url = result['redirectTO']
                    logger.info(f"✅ Payment created!")
                    return payment_url
                else:
                    logger.error(f"❌ API error!")
                    return None
                    
    except Exception as e:
        logger.error(f"❌ Payment error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None

async def fetch_payerurl_qr_and_note(payment_url: str, order_id: str) -> dict:
    """
    🔥 AUTO-FETCH QR CODE AND NOTE FROM PAYERURL PAYMENT PAGE
    Uses Playwright to auto-click Binance button and extract QR + note
    SLOW AND PATIENT VERSION - Waits properly at each step!
    """
    try:
        from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
        
        logger.info(f"🎭 Starting SLOW & PATIENT Playwright for {order_id}")
        logger.info(f"   URL: {payment_url}")
        
        async with async_playwright() as p:
            # Launch browser
            browser = await p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox']
            )
            context = await browser.new_context(
                viewport={'width': 800, 'height': 1400},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            page = await context.new_page()
            
            # STEP 1: Navigate and wait for FULL page load
            logger.info(f"📄 STEP 1: Opening payment page...")
            try:
                await page.goto(payment_url, wait_until='networkidle', timeout=60000)
            except Exception as e:
                logger.error(f"❌ Failed to load page: {e}")
                await browser.close()
                return {'success': False, 'qr_file': None, 'note': None}
            
            # Wait LONGER for JavaScript to execute
            logger.info(f"⏳ Waiting 8 seconds for page to fully load...")
            await asyncio.sleep(8)
            
            # Take screenshot
            try:
                await page.screenshot(path=f'/tmp/page_step1_{order_id}.png', full_page=True)
                logger.info(f"📸 Step 1 screenshot saved")
            except:
                pass
            
            # STEP 2: Find and click Binance - BE VERY PATIENT!
            logger.info(f"🔍 STEP 2: Searching for Binance option...")
            clicked = False
            
            # Wait for page to have content
            await page.wait_for_load_state('domcontentloaded')
            await asyncio.sleep(2)
            
            # Try multiple times to find and click
            for attempt in range(3):
                logger.info(f"   Attempt {attempt + 1}/3 to find Binance...")
                
                try:
                    # Look for Binance container/card
                    binance_selectors = [
                        # Try parent container of Binance text
                        'div:has-text("Binance"):has-text("USDT")',
                        'div:has-text("Binance"):has-text("Including")',
                        
                        # Try any clickable element with Binance
                        '[role="button"]:has-text("Binance")',
                        'button:has-text("Binance")',
                        'a:has-text("Binance")',
                        
                        # Try by image
                        'img[alt*="Binance" i] >> xpath=..',
                        
                        # Generic
                        'text=Binance',
                        'div:has-text("Binance")',
                    ]
                    
                    for selector in binance_selectors:
                        try:
                            logger.info(f"      Trying: {selector}")
                            
                            # Wait for element with long timeout
                            element = await page.wait_for_selector(
                                selector, 
                                state='visible',
                                timeout=10000
                            )
                            
                            if element:
                                # Check if it's really visible
                                box = await element.bounding_box()
                                if box:
                                    logger.info(f"✅ Found Binance element at position: x={box['x']}, y={box['y']}")
                                    
                                    # Scroll to element first
                                    await element.scroll_into_view_if_needed()
                                    await asyncio.sleep(1)
                                    
                                    # Try to click
                                    logger.info(f"👆 Clicking Binance...")
                                    await element.click(timeout=10000)
                                    
                                    clicked = True
                                    logger.info(f"✅ Clicked Binance successfully!")
                                    break
                        
                        except Exception as e:
                            logger.debug(f"      Selector failed: {str(e)[:80]}")
                            continue
                    
                    if clicked:
                        break
                    
                    # Wait before retry
                    await asyncio.sleep(3)
                
                except Exception as e:
                    logger.warning(f"   Attempt {attempt + 1} error: {e}")
                    await asyncio.sleep(3)
            
            # If clicking failed, try JavaScript as last resort
            if not clicked:
                logger.info(f"🔄 Last resort: JavaScript click...")
                try:
                    clicked = await page.evaluate('''() => {
                        const elements = document.querySelectorAll('*');
                        for (let el of elements) {
                            const text = el.textContent || '';
                            if (text.includes('Binance') && el.offsetParent) {
                                el.click();
                                return true;
                            }
                        }
                        return false;
                    }''')
                    if clicked:
                        logger.info(f"✅ JavaScript click worked!")
                except Exception as js_err:
                    logger.error(f"❌ JavaScript click failed: {js_err}")
            
            if not clicked:
                logger.error(f"❌ Could not click Binance after all attempts!")
                await browser.close()
                return {'success': False, 'qr_file': None, 'note': None}
            
            # STEP 3: Wait for NEW page to load after click
            logger.info(f"⏳ STEP 3: Waiting for payment page to load after click...")
            
            # Wait for navigation/page update
            try:
                await page.wait_for_load_state('networkidle', timeout=30000)
            except:
                pass
            
            # Wait LONG time for QR page to fully load
            logger.info(f"⏳ Waiting 10 seconds for QR page...")
            await asyncio.sleep(10)
            
            # Take screenshot
            try:
                await page.screenshot(path=f'/tmp/page_step3_{order_id}.png', full_page=True)
                logger.info(f"📸 Step 3 screenshot saved")
            except:
                pass
            
            # STEP 4: Extract QR code
            logger.info(f"🔍 STEP 4: Extracting QR code...")
            qr_file_path = None
            
            try:
                # Get ALL images
                images = await page.query_selector_all('img')
                logger.info(f"   Found {len(images)} images on page")
                
                for idx, img in enumerate(images):
                    try:
                        src = await img.get_attribute('src')
                        alt = await img.get_attribute('alt') or ''
                        
                        if not src:
                            continue
                        
                        logger.info(f"   Image {idx}: {src[:100]}")
                        
                        # Download if it looks like QR
                        if src.startswith('http'):
                            async with aiohttp.ClientSession() as session:
                                async with session.get(src, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                                    if resp.status == 200:
                                        qr_data = await resp.read()
                                        if len(qr_data) > 1000:  # Should be decent size
                                            qr_file_path = f"/tmp/qr_crypto_{order_id}.png"
                                            with open(qr_file_path, 'wb') as f:
                                                f.write(qr_data)
                                            logger.info(f"✅ QR saved: {len(qr_data)} bytes")
                                            break
                        elif src.startswith('data:image'):
                            qr_data_b64 = src.split(',')[1]
                            qr_data = base64.b64decode(qr_data_b64)
                            if len(qr_data) > 1000:
                                qr_file_path = f"/tmp/qr_crypto_{order_id}.png"
                                with open(qr_file_path, 'wb') as f:
                                    f.write(qr_data)
                                logger.info(f"✅ QR saved: {len(qr_data)} bytes")
                                break
                    except Exception as img_err:
                        logger.debug(f"   Image {idx} error: {img_err}")
                        continue
            except Exception as qr_err:
                logger.error(f"❌ QR extraction error: {qr_err}")
            
            # STEP 5: Extract note
            logger.info(f"🔍 STEP 5: Extracting payment note...")
            note = None
            
            try:
                # Get visible text
                body_text = await page.evaluate('() => document.body.innerText')
                
                # Save HTML
                html = await page.content()
                with open(f'/tmp/page_html_{order_id}.html', 'w', encoding='utf-8') as f:
                    f.write(html)
                
                # Look for 6-8 digit number near "Add Note"
                if 'Add Note' in body_text or 'add note' in body_text.lower():
                    matches = re.findall(r'(?:Add Note|add note)[:\s]{0,50}(\d{6,8})', body_text, re.IGNORECASE)
                    if matches:
                        note = matches[0]
                        logger.info(f"✅ Note found: {note}")
                
                # Fallback: get all 6-8 digit numbers
                if not note:
                    all_nums = re.findall(r'\b(\d{6,8})\b', body_text)
                    filtered = [n for n in all_nums if n not in ['556471644', order_id.replace('ORD', '')]]
                    if filtered:
                        note = filtered[0]
                        logger.info(f"✅ Note found (filtered): {note}")
                
            except Exception as note_err:
                logger.error(f"❌ Note extraction error: {note_err}")
            
            await browser.close()
            
            # Final result
            if not qr_file_path or not note:
                logger.error(f"❌ Extraction incomplete: QR={'✅' if qr_file_path else '❌'}, Note={'✅' if note else '❌'}")
                return {'success': False, 'qr_file': qr_file_path, 'note': note or f"ERROR_{order_id}"}
            
            result = {
                'success': True,
                'qr_file': qr_file_path,
                'note': note
            }
            
            logger.info(f"🎉 SUCCESS! QR + Note extracted!")
            return result
            
    except Exception as e:
        logger.error(f"❌ Playwright error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {'success': False, 'qr_file': None, 'note': None}
            
async def get_custom_price(user_id: int, variant_id: int) -> Optional[float]:
    """Get custom price for a specific user and variant"""
    try:
        result = await db.fetchrow("""
            SELECT custom_price FROM custom_pricing 
            WHERE user_id = $1 AND variant_id = $2 AND is_active = TRUE
        """, user_id, variant_id)
        
        if result:
            price = float(result['custom_price'])
            logger.info(f"✅ Custom price for user {user_id}, variant {variant_id}: ${price:.2f}")
            return price
        else:
            logger.info(f"ℹ️ No custom price for user {user_id}, variant {variant_id}")
            return None
    except Exception as e:
        logger.error(f"❌ Error getting custom price for user {user_id}, variant {variant_id}: {e}")
        return None

async def get_reseller_discount(user_id: int) -> float:
    """Get reseller discount percentage for a user"""
    try:
        result = await db.fetchrow("""
            SELECT discount_percent FROM resellers 
            WHERE user_id = $1 AND is_active = TRUE
        """, user_id)
        
        if result:
            discount = float(result['discount_percent'])
            logger.info(f"✅ Reseller discount for user {user_id}: {discount}%")
            return discount
        else:
            logger.info(f"ℹ️ No reseller discount for user {user_id}")
            return 0.0
    except Exception as e:
        logger.error(f"❌ Error getting reseller discount for user {user_id}: {e}")
        return 0.0

async def calculate_final_price(user_id: int, variant_id: int, base_price: float) -> tuple[float, str]:
    """Calculate final price with custom pricing AND reseller discount
    If BOTH exist, uses whichever is BETTER for the customer!
    Returns: (final_price, price_note)
    """
    try:
        logger.info(f"📊 Calculating price for user {user_id}, variant {variant_id}, base ${base_price:.2f}")
        
        # Get custom price
        custom_price = await get_custom_price(user_id, variant_id)
        
        # Get reseller discount
        discount = await get_reseller_discount(user_id)
        reseller_price = base_price * (1 - discount / 100) if discount > 0 else None
        
        # Determine final price - use BEST price for customer!
        prices = []
        
        if custom_price is not None:
            prices.append((custom_price, f"💎 Special Price"))
            logger.info(f"💎 Custom price available: ${custom_price:.2f}")
        
        if reseller_price is not None:
            prices.append((reseller_price, f"🎁 Reseller {discount}%"))
            logger.info(f"🎁 Reseller price available: ${reseller_price:.2f} ({discount}% off)")
        
        if not prices:
            # No special pricing, use base price
            logger.info(f"💰 Using base price: ${base_price:.2f}")
            return base_price, ""
        
        # Return the LOWEST price (best for customer!)
        final_price, note = min(prices, key=lambda x: x[0])
        logger.info(f"✅ Final price: ${final_price:.2f} ({note})")
        return final_price, note
        
    except Exception as e:
        logger.error(f"❌ Error calculating final price for user {user_id}: {e}")
        # Return base price on error - never break the flow!
        return base_price, ""

# ═══════════════════════════════════════════════════════════
# 🎮 CLIENT SIDE - 100% BUTTON BASED!
# ═══════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command - phone verification"""
    user = update.effective_user
    
    # Save user
    await db.execute("""
        INSERT INTO users (user_id, first_name, username, last_active)
        VALUES ($1, $2, $3, CURRENT_TIMESTAMP)
        ON CONFLICT (user_id) DO UPDATE
        SET first_name = $2, username = $3, last_active = CURRENT_TIMESTAMP
    """, user.id, user.first_name, user.username)
    
    # Check if admin
    is_admin = (user.id in ADMIN_IDS)
    
    # Check blocked
    is_blocked = await db.fetchval("SELECT is_blocked FROM users WHERE user_id = $1", user.id)
    if is_blocked:
        await update.message.reply_text("🚫 You have been blocked.")
        return
    
    # Check phone
    phone = await db.fetchval("SELECT phone_number FROM users WHERE user_id = $1", user.id)
    
    if not phone:
        keyboard = [[KeyboardButton("📱 Share Phone Number", request_contact=True)]]
        
        welcome_text = (
            f"\n"
            f"   🛒 *LICENSE KEY SHOP*   \n"
            f"\n\n"
            f"👋 *Welcome, {user.first_name}!*\n\n"
        )
        
        if is_admin:
            welcome_text += (
                f"🔐 *You are the ADMIN!*\n\n"
                f"After verification, you'll see the admin panel automatically.\n\n"
            )
        
        welcome_text += (
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🔒 *VERIFICATION REQUIRED*\n"
            f"━━━━━━━━━━━━━━━━━━━\n\n"
            f"To start shopping, please verify your phone number.\n\n"
            f"✅ *Why we need this:*\n"
            f"• Secure your purchases\n"
            f"• Deliver keys to you\n"
            f"• Protect your account\n\n"
            f"👇 *Click the button below:*"
        )
        
        await update.message.reply_text(
            welcome_text,
            reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
            parse_mode="Markdown"
        )
        return
    
    # 🔐 AUTO-DETECT ADMIN - Show admin panel if admin!
    if is_admin:
        await admin_panel(update, context)
    else:
        await show_main_menu(update, context, user.first_name)

async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle phone verification"""
    user = update.effective_user
    contact = update.message.contact
    
    if contact and contact.user_id == user.id:
        await db.execute(
            "UPDATE users SET phone_number = $1 WHERE user_id = $2",
            contact.phone_number, user.id
        )
        
        is_admin = (user.id in ADMIN_IDS)
        
        if is_admin:
            response_text = (
                "\n"
                "   ✅ *VERIFIED!*   \n"
                "\n\n"
                "🔐 *You are the ADMIN!*\n\n"
                "📋 *Admin Panel Auto-Opening!*\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                "✨ Manage products & keys\n"
                "💎 Set custom prices\n"
                "🎁 Manage reseller discounts\n"
                "📊 View analytics & orders\n"
                "━━━━━━━━━━━━━━━━━━━\n\n"
                "*Loading your admin panel...* 🚀"
            )
        else:
            response_text = (
                "\n"
                "   ✅ *VERIFIED!*   \n"
                "\n\n"
                "🎉 *Verification Complete!*\n\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                "🛒 *You can now shop!*\n"
                "💎 *Browse premium keys*\n"
                "⚡ *Instant delivery*\n"
                "🔒 *100% Secure*\n"
                "━━━━━━━━━━━━━━━━━━━\n\n"
                "*Opening shop...* 🚀"
            )
        
        await update.message.reply_text(
            response_text,
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        
        # 🔐 AUTO-SHOW ADMIN PANEL if admin, else show shop
        if is_admin:
            await admin_panel(update, context)
        else:
            await show_main_menu(update, context, user.first_name)
    else:
        await update.message.reply_text("❌ Please share YOUR phone number.")

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, name: str = "User"):
    """Display main product menu with buttons"""
    
    user_id = update.effective_user.id if update.effective_user else 0
    is_admin = (user_id in ADMIN_IDS)
    
    # Auto-get name if not provided
    if name == "User" and user_id:
        user_data = await db.fetchrow("SELECT first_name FROM users WHERE user_id = $1", user_id)
        if user_data and user_data['first_name']:
            name = user_data['first_name']
        else:
            name = update.effective_user.first_name or "User"
    
    products = await db.fetch("SELECT * FROM products WHERE is_active = TRUE ORDER BY id")
    
    if not products:
        text = "*🏪 License Key Shop*\n\n⚠️ No products available.\n\n"
        
        if is_admin:
            text += "🔐 *You're the ADMIN!*\n\n"
            text += "📋 *To add products:*\n"
            text += "1. Send `/admin`\n"
            text += "2. Click 'Manage Products'\n"
            text += "3. Add your first product!\n\n"
            text += "💡 *Start building your shop now!*"
        else:
            text += "Check back later!"
        
        keyboard = []
    else:
        text = (
            f"\n"
            f"   🛒 *LICENSE KEY SHOP*   \n"
            f"\n\n"
            f"👋 *Welcome back, {name}!*\n\n"
            f"🎮 *CHOOSE YOUR GAME:*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"💎 *Premium Keys Available*\n"
            f"⚡ *Instant Delivery*\n"
            f"🔒 *100% Safe & Secure*\n"
            f"━━━━━━━━━━━━━━━━━━━"
        )
        
        keyboard = []
        for product in products:
            # FIXED: 1 product per row!
            btn_text = f"🛒 {product['name']}"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"prod_{product['id']}")])
        
        # Add stylish bottom buttons - only essential emojis
        keyboard.append([InlineKeyboardButton("📦 My Orders", callback_data="my_orders")])
        keyboard.append([InlineKeyboardButton("🔄 Refresh Shop", callback_data="start_menu")])
    
    markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")
        await update.callback_query.answer()
    else:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")

async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's purchase history with keys - Bangladesh time (AM/PM)"""
    query = update.callback_query
    user_id = query.from_user.id
    
    await query.answer()
    
    # Get user's orders
    orders = await db.fetch("""
        SELECT 
            s.order_id,
            s.product_name,
            s.variant_name,
            s.amount,
            s.key_delivered,
            s.sale_date
        FROM sales s
        WHERE s.user_id = $1
        ORDER BY s.sale_date DESC
        LIMIT 20
    """, user_id)
    
    if not orders:
        text = (
            f"\n"
            f"   🛍️ *MY ORDERS*   \n"
            f"\n\n"
            f"📭 *No orders yet!*\n\n"
            f"🛒 Start shopping to see your orders here!\n"
            f"💎 All your keys will be saved here"
        )
        keyboard = [[InlineKeyboardButton("🏠 Back to Shop 🛍️", callback_data="start_menu")]]
    else:
        text = (
            f"\n"
            f"   🛍️ *MY ORDERS*   \n"
            f"\n\n"
            f"📊 *Total Orders: {len(orders)}*\n"
            f"━━━━━━━━━━━━━━━━━━━\n\n"
        )
        
        for idx, order in enumerate(orders, 1):
            # Convert UTC to Bangladesh time (UTC+6)
            from datetime import timedelta
            sale_date_utc = order['sale_date']
            sale_date_bd = sale_date_utc + timedelta(hours=6)
            
            # Format as AM/PM
            time_str = sale_date_bd.strftime("%d %b %Y, %I:%M %p")
            
            text += (
                f"🛒 *ORDER #{idx}*\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"🎮 *Product:* {order['product_name']}\n"
                f"⏱️ *Duration:* {order['variant_name']}\n"
                f"💰 *Paid:* ₹{int(order['amount'])}\n"
                f"📅 *Date:* {time_str} BST\n\n"
                f"🔑 *LICENSE KEY:*\n"
                f"`{order['key_delivered']}`\n\n"
            )
        
        text += (
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"✨ *Keep your keys safe!*\n"
            f"💎 *Thank you for choosing us!*"
        )
        
        keyboard = [[InlineKeyboardButton("🛒 Continue Shopping 🛍️", callback_data="start_menu")]]
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def show_variants(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show stock & prices for selected product"""
    query = update.callback_query
    product_id = int(query.data.split("_")[1])
    
    product = await db.fetchrow("SELECT * FROM products WHERE id = $1", product_id)
    
    if not product:
        await query.answer("❌ Product not found!", show_alert=True)
        return
    
    variants = await db.fetch("""
        SELECT v.*, 
               COUNT(k.id) FILTER (WHERE k.status = 'available') as stock
        FROM variants v
        LEFT JOIN keys k ON k.variant_id = v.id
        WHERE v.product_id = $1 AND v.is_active = TRUE
        GROUP BY v.id
        ORDER BY v.validity_days
    """, product_id)
    
    if not variants:
        await query.answer("❌ No variants available!", show_alert=True)
        return
    
    # Professional formatting
    user_id = query.from_user.id
    text = (
        f"\n"
        f"   🛒 *{product['name'].upper()}*   \n"
        f"\n\n"
        f"📊 *STOCK & PRICING:*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
    )
    
    for var in variants:
        emoji = "✅" if var['stock'] > 5 else "⚠️" if var['stock'] > 0 else "❌"
        stock_text = "In Stock" if var['stock'] > 5 else f"{var['stock']} Left" if var['stock'] > 0 else "Out of Stock"
        
        # Calculate final price with custom pricing/discounts
        final_price, price_note = await calculate_final_price(user_id, var['id'], var['price'])
        final_inr_price = int(final_price * USD_TO_INR)
        
        # Show with ├ for better organization
        text += (
            f"{emoji} *{var['name']}*\n"
            f"├ 📦 Stock: {stock_text}\n"
            f"└ 💰 Price: *₹{final_inr_price}*\n\n"
        )
    
    text += (
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 *SELECT YOUR PLAN:*"
    )
    
    keyboard = []
    for var in variants:
        if var['stock'] > 0:
            # Calculate final price for button
            final_price, price_note = await calculate_final_price(user_id, var['id'], var['price'])
            final_inr_price = int(final_price * USD_TO_INR)
            
            # Clean button with dash separator
            btn_text = f"🛒 Buy {var['name']} - ₹{final_inr_price}"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"buy_{var['id']}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Back to Shop", callback_data="start_menu")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def initiate_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show payment method selection - UPI or Crypto"""
    query = update.callback_query
    variant_id = int(query.data.split("_")[1])
    user_id = query.from_user.id
    
    await query.answer("⏳ Loading payment options...")
    
    variant = await db.fetchrow("""
        SELECT v.*, p.name as product_name, p.emoji, p.id as product_id
        FROM variants v
        JOIN products p ON p.id = v.product_id
        WHERE v.id = $1
    """, variant_id)
    
    if not variant:
        await query.edit_message_text("❌ Variant not found!")
        return
    
    # Check stock
    stock = await get_stock_count(variant_id)
    if stock == 0:
        await query.edit_message_text(
            f"*❌ OUT OF STOCK*\n\n{variant['emoji']} {variant['product_name']} ├ {variant['name']}\n\nPlease choose another option.",
            parse_mode="Markdown"
        )
        return
    
    # Calculate prices
    final_price_usd, price_note = await calculate_final_price(user_id, variant_id, variant['price'])
    
    # IMPORTANT: Different currencies for different payment methods!
    inr_amount = int(final_price_usd * USD_TO_INR)  # UPI = INR (₹900)
    usdt_amount = final_price_usd                    # Crypto = USDT ($10.00) - NO CONVERSION!
    
    text = (
        f"\n"
        f"   💳 *PAYMENT METHOD*   \n"
        f"\n\n"
        f"{variant['emoji']} *{variant['product_name']}*\n"
        f"⏱️ *Duration:* {variant['name']}\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"💰 *CHOOSE PAYMENT METHOD:*"
    )
    
    bdt_amount = int(final_price_usd * USD_TO_BDT)  # bKash = BDT
    
    keyboard = [
        [InlineKeyboardButton(f"🇮🇳 UPI India - ₹{inr_amount}", callback_data=f"pay_upi_{variant_id}")],
        [InlineKeyboardButton(f"💎 Crypto Payment - ${usdt_amount:.2f} USDT", callback_data=f"pay_crypto_{variant_id}")],
        [InlineKeyboardButton(f"🇧🇩 bKash - {bdt_amount} BDT", callback_data=f"pay_bkash_{variant_id}")],
        [InlineKeyboardButton("🔙 Back", callback_data=f"prod_{variant['product_id']}")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def initiate_upi_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create UPI payment and show QR code - AUTO EXPIRES in 10 min + AUTO POLLING"""
    query = update.callback_query
    variant_id = int(query.data.split("_")[2])
    user_id = query.from_user.id
    
    await query.answer("⏳ Creating UPI payment...")
    
    variant = await db.fetchrow("""
        SELECT v.*, p.name as product_name, p.emoji
        FROM variants v
        JOIN products p ON p.id = v.product_id
        WHERE v.id = $1
    """, variant_id)
    
    if not variant:
        await query.edit_message_text("❌ Variant not found!")
        return
    
    # Check stock
    stock = await get_stock_count(variant_id)
    if stock == 0:
        await query.edit_message_text(
            f"*❌ OUT OF STOCK*\n\n{variant['emoji']} {variant['product_name']} ├ {variant['name']}\n\nPlease choose another option.",
            parse_mode="Markdown"
        )
        return
    
    # Get phone
    phone = await db.fetchval("SELECT phone_number FROM users WHERE user_id = $1", user_id)
    if not phone:
        await query.edit_message_text("❌ Phone required. Use /start again.")
        return
    
    # Create order
    order_id = f"ORD{int(time.time())}{user_id % 10000}"
    
    await query.edit_message_text("⏳ *Processing payment...*", parse_mode="Markdown")
    
    # Calculate final price with custom pricing/discounts
    final_price_usd, price_note = await calculate_final_price(user_id, variant_id, variant['price'])
    
    # Convert final USD price to INR - CRITICAL: Use int() to match payment API behavior!
    inr_amount = int(final_price_usd * USD_TO_INR)
    
    # Create payment with final INR amount (UPI only supports INR)
    payment_url = await create_payment_order(user_id, inr_amount, order_id, phone)
    
    if not payment_url:
        await query.edit_message_text("❌ *Payment Error*\n\nPlease try again.", parse_mode="Markdown")
        return
    
    # Save order - CRITICAL FIX: Store INR amount (not USD) so webhook comparison works!
    # Webhook will send INR, database has INR, comparison succeeds!
    await db.execute("""
        INSERT INTO orders (order_id, user_id, variant_id, amount, payment_method, payment_url, qr_message_id, chat_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    """, order_id, user_id, variant_id, inr_amount, 'upi', payment_url, query.message.message_id, query.message.chat_id)
    
    # Extract QR code (EXACTLY like x.php - try base64 AND img tag!)
    qr_file = None
    try:
        logger.info(f"🌐 Fetching payment page: {payment_url}")
        async with aiohttp.ClientSession() as session:
            async with session.get(payment_url, timeout=aiohttp.ClientTimeout(total=10), ssl=False) as resp:
                html = await resp.text()
                logger.info(f"📄 HTML length: {len(html)} chars, Status: {resp.status}")
                
                # Log first 500 chars to see if there's an error message
                if "expired" in html.lower() or "error" in html.lower():
                    logger.error(f"⚠️ Payment page shows error: {html[:500]}")
                
                # Method 1: Base64 inline image (same as before)
                match = re.search(r'data:image/[^;]+;base64,([A-Za-z0-9+/=]+)', html)
                if match:
                    qr_data = base64.b64decode(match.group(1))
                    qr_file = f"/tmp/qr_{order_id}.png"
                    with open(qr_file, 'wb') as f:
                        f.write(qr_data)
                    if os.path.getsize(qr_file) < 100:
                        os.remove(qr_file)
                        qr_file = None
                    else:
                        logger.info(f"✅ QR extracted: base64")
                
                # Method 2: <img> tag URL (like x.php!)
                if not qr_file:
                    img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\'][^>]*>', html, re.IGNORECASE)
                    if img_match:
                        img_url = img_match.group(1)
                        
                        # Fix relative URLs
                        if img_url.startswith('//'):
                            img_url = 'https:' + img_url
                        elif not img_url.startswith('http'):
                            from urllib.parse import urljoin
                            img_url = urljoin(payment_url, img_url)
                        
                        logger.info(f"🖼️ Trying img URL: {img_url}")
                        
                        # Download image
                        try:
                            async with session.get(img_url, timeout=aiohttp.ClientTimeout(total=10), ssl=False) as img_resp:
                                img_data = await img_resp.read()
                                if img_data and len(img_data) > 100:
                                    qr_file = f"/tmp/qr_{order_id}.png"
                                    with open(qr_file, 'wb') as f:
                                        f.write(img_data)
                                    logger.info(f"✅ QR extracted: img tag")
                        except Exception as e:
                            logger.error(f"❌ Img download failed: {e}")
                
                if not qr_file:
                    logger.warning(f"⚠️ No QR found in HTML. Sending payment link instead.")
                    
    except Exception as e:
        logger.error(f"❌ QR extraction error: {e}")
    
    # Professional message - NO special price shown to customer!
    text = (
        f"\n"
        f"   🛒 *CHECKOUT*   \n"
        f"\n\n"
        f"💳 *SCAN QR & PAY ₹{inr_amount:.0f}*\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"{variant['emoji']} *Product:* {variant['product_name']}\n"
        f"⏱️ *Duration:* {variant['name']}\n"
        f"💰 *Amount:* ₹{inr_amount:.0f}\n"
        f"📝 *Order ID:* `{order_id}`\n"
        f"⚡ *COMPLETE PAYMENT IN 5 MINUTES!*\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🔑 *Key will be deliver In 5 Minutes*"
    )
    
    # NO CHECK BUTTON - REMOVED AS REQUESTED!
    keyboard = []
    
    try:
        if qr_file and os.path.exists(qr_file):
            # Delete old message
            await context.bot.delete_message(chat_id=query.message.chat_id, message_id=query.message.message_id)
            
            # Send QR image
            sent_msg = await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=open(qr_file, 'rb'),
                caption=text,
                reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
                parse_mode="Markdown"
            )
            
            # Update message ID
            await db.execute("UPDATE orders SET qr_message_id = $1 WHERE order_id = $2", sent_msg.message_id, order_id)
            os.remove(qr_file)
        else:
            text += f"\n\n🔗 [Click to Pay]({payment_url})"
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"QR error: {e}")
        text += f"\n\n🔗 [Pay Now]({payment_url})"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None, parse_mode="Markdown")
    
    # Start 5-minute timer
    asyncio.create_task(payment_timer(context, order_id, query.message.chat_id))
    
    # 🚀 NEW FEATURE: AUTO-POLLING EVERY 10 SECONDS!
    asyncio.create_task(auto_check_payment(context, order_id))

async def initiate_crypto_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """SIMPLE Crypto payment via PayerURL - JUST LINK & INSTRUCTIONS"""
    query = update.callback_query
    variant_id = int(query.data.split("_")[2])
    user_id = query.from_user.id
    user_name = query.from_user.first_name or "Customer"
    
    await query.answer("⏳ Creating payment link...")
    
    variant = await db.fetchrow("""
        SELECT v.*, p.name as product_name, p.emoji
        FROM variants v
        JOIN products p ON p.id = v.product_id
        WHERE v.id = $1
    """, variant_id)
    
    if not variant:
        await query.edit_message_text("❌ Variant not found!")
        return
    
    # Check stock
    stock = await get_stock_count(variant_id)
    if stock == 0:
        await query.edit_message_text(
            f"*❌ OUT OF STOCK*\n\n{variant['emoji']} {variant['product_name']} ├ {variant['name']}",
            parse_mode="Markdown"
        )
        return
    
    # Create order
    order_id = f"ORD{int(time.time())}{user_id % 10000}"
    
    await query.edit_message_text("⏳ *Creating payment link...*\n\nPlease wait...", parse_mode="Markdown")
    
    # Calculate final price in USD
    final_price_usd, price_note = await calculate_final_price(user_id, variant_id, variant['price'])
    
    # Create PayerURL payment with USD amount
    payment_url = await create_payerurl_payment(user_id, final_price_usd, order_id, user_name, variant['product_name'])
    
    if not payment_url:
        await query.edit_message_text("❌ *Payment Error*\n\nPlease try again.", parse_mode="Markdown")
        return
    
    # Save order with crypto payment method
    await db.execute("""
        INSERT INTO orders (order_id, user_id, variant_id, amount, payment_method, payment_url, qr_message_id, chat_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    """, order_id, user_id, variant_id, final_price_usd, 'crypto', payment_url, query.message.message_id, query.message.chat_id)
    
    logger.info(f"💎 PayerURL payment created for {order_id}: {payment_url}")
    
    # SIMPLE MESSAGE - JUST LINK & INSTRUCTIONS!
    text = (
        f"\n"
        f"   💳 *CRYPTO PAYMENT - USDT*   \n"
        f"\n\n"
        f"{variant['emoji']} *Product:* {variant['product_name']}\n"
        f"⏱️ *Duration:* {variant['name']}\n"
        f"💰 *Amount:* ${final_price_usd:.2f} USDT\n"
        f"📝 *Order ID:* `{order_id}`\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"*📋 SIMPLE INSTRUCTIONS:*\n\n"
        f"1️⃣ Click *'💳 Pay Now'* button below\n\n"
        f"2️⃣ Complete payment on PayerURL website\n\n"
        f"3️⃣ Come back here and wait\n\n"
        f"4️⃣ Your key will arrive automatically! 🎉\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⏱ *Time Limit:* 10 minutes\n\n"
        f"💡 *System will auto-detect payment & send key!*"
    )
    
    keyboard = [[InlineKeyboardButton("💳 Pay Now", url=payment_url)]]
    
    try:
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
        logger.info(f"✅ Payment link sent to user {user_id}")
    except Exception as e:
        logger.error(f"Crypto payment message error: {e}")
        import traceback
        logger.error(traceback.format_exc())
    
    # Start timer - payment will expire after 10 minutes
    asyncio.create_task(payment_timer(context, order_id, query.message.chat_id))

async def auto_check_payment(context: ContextTypes.DEFAULT_TYPE, order_id: str):
    """Auto-check payment status every 10 seconds (NO CHECK BUTTON NEEDED!)"""
    logger.info(f"🔄 Auto-polling started for {order_id}")
    
    for i in range(60):  # Check 60 times = 10 minutes
        await asyncio.sleep(10)  # Wait 10 seconds
        
        # Check if order still pending
        order = await db.fetchrow("SELECT * FROM orders WHERE order_id = $1", order_id)
        
        if not order or order['status'] != 'pending':
            logger.info(f"✅ Auto-polling stopped for {order_id} (status: {order['status'] if order else 'deleted'})")
            return
        
        # Check payment status with API
        result = await check_payment_status(order_id)
        
        if result.get('success'):
            status = result.get('status', 'PENDING')
            
            if status.upper() == 'SUCCESS':
                utr = result.get('utr', '')
                logger.info(f"✅ Auto-polling found payment: {order_id}")
                
                # Process payment
                await process_successful_payment(context, order_id, utr)
                return
        
        logger.info(f"⏳ Auto-poll {i+1}/60 for {order_id}")
    
    logger.info(f"⏱ Auto-polling finished for {order_id}")

async def payment_timer(context: ContextTypes.DEFAULT_TYPE, order_id: str, chat_id: int):
    """Auto-expire QR after 10 minutes"""
    await asyncio.sleep(PAYMENT_TIMEOUT)
    
    order = await db.fetchrow("SELECT * FROM orders WHERE order_id = $1 AND status = 'pending'", order_id)
    
    if order:
        await db.execute("UPDATE orders SET status = 'expired' WHERE order_id = $1", order_id)
        
        try:
            # Delete QR message
            await context.bot.delete_message(chat_id=chat_id, message_id=order['qr_message_id'])
            logger.info(f"✅ QR expired & deleted: {order_id}")
        except:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=order['qr_message_id'],
                    text=f"*❌ Payment Expired*\n\n📝 {order_id}\n\nTime's up (10 min). Please create a new order.",
                    parse_mode="Markdown"
                )
            except:
                pass

# ═══════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════
# 📱 BKASH PAYMENT - BANGLADESH
# ═══════════════════════════════════════════════════════════

async def initiate_bkash_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Initiate bKash payment"""
    query = update.callback_query
    await query.answer()
    
    variant_id = int(query.data.split('_')[2])
    user_id = query.from_user.id
    
    variant = await db.fetchrow(
        """
        SELECT v.*, p.name as product_name, p.emoji, p.id as product_id
        FROM variants v
        JOIN products p ON v.product_id = p.id
        WHERE v.id = $1
        """,
        variant_id
    )
    
    if not variant:
        await query.edit_message_text("❌ Variant not found!")
        return
    
    # Check custom pricing
    custom_price = await db.fetchval(
        "SELECT custom_price FROM custom_pricing WHERE user_id = $1 AND variant_id = $2",
        user_id, variant_id
    )
    
    price_usd = float(custom_price if custom_price else variant['price'])
    price_bdt = price_usd * USD_TO_BDT
    
    # Create order ID
    order_id = f"BKASH_{user_id}_{int(time.time())}"
    
    # Generate transaction reference code
    txn_ref = ''.join(random.choices('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', k=10))
    
    # Save to database
    await db.execute(
        """
        INSERT INTO orders (order_id, user_id, variant_id, product_name, variant_name, 
                           price, currency, payment_method, status, created_at, expires_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        """,
        order_id, user_id, variant_id, variant['product_name'], variant['name'],
        price_bdt, 'BDT', 'bkash', 'pending',
        datetime.now(), datetime.now() + timedelta(seconds=PAYMENT_TIMEOUT)
    )
    
    emoji = variant['emoji'] or '🎮'
    
    text = f"""
{emoji} **JIBON MODZ**
🧾 **Invoice ID:** `{txn_ref}`

💰 **Pay {int(price_bdt)} BDT** _(Charge 0)_

📱 **ট্রান্জেকশন আইডি দিন**

**{txn_ref}**

• *247# ডায়াল করে আপনার bkash মোবাইল মেনুতে যান অথবা bkash অ্যাপে যান।

• "Send Money" -এ ক্লিক করুন।

• প্রাপক নম্বর হিসেবে এই নম্বরটি লিখুন:
  **{BKASH_PHONE}** 📋

• টাকার পরিমাণ: **{int(price_bdt)} BDT**

• বিস্তিত করতে এখন আপনার bkash মোবাইল মেনু পিন লিখুন।

• সবকিছু ঠিক থাকলে, আপনি bkash থেকে একটি নিশ্চিতকরণ বার্তা পাবেন।

• এখন উপরের বক্সে আপনার **Transaction ID** দিন এবং নিচের **VERIFY** বাটনে ক্লিক করুন।

⏱️ **Payment expires in 10 minutes**
"""
    
    keyboard = [
        [InlineKeyboardButton("✅ I PAID - Enter TRX ID", callback_data=f"bkash_verify_{order_id}")],
        [InlineKeyboardButton("❌ Cancel Order", callback_data=f"prod_{variant['product_id']}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
    if 'bkash_orders' not in context.bot_data:
        context.bot_data['bkash_orders'] = {}
    context.bot_data['bkash_orders'][user_id] = order_id

async def bkash_verify_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt user to enter transaction ID"""
    query = update.callback_query
    await query.answer()
    
    order_id = '_'.join(query.data.split('_')[2:])
    user_id = query.from_user.id
    
    order = await db.fetchrow("SELECT * FROM orders WHERE order_id = $1 AND user_id = $2", order_id, user_id)
    if not order:
        await query.edit_message_text("❌ Order not found!")
        return
    
    if order['status'] != 'pending':
        await query.edit_message_text(f"❌ Order already {order['status']}!")
        return
    
    if 'bkash_orders' not in context.bot_data:
        context.bot_data['bkash_orders'] = {}
    context.bot_data['bkash_orders'][user_id] = order_id
    
    text = f"""
📱 **bKash Payment Verification**

🆔 **Order ID:** `{order_id}`

Please send me your **Transaction ID** (TRX ID) from the bKash confirmation message.

Example: `CKL9EU0SPR`

⏱️ Waiting for your Transaction ID...
"""
    
    await query.edit_message_text(text, parse_mode='Markdown')

async def handle_bkash_txn_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle bKash transaction ID input"""
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    if 'bkash_orders' not in context.bot_data:
        return
    
    if user_id not in context.bot_data['bkash_orders']:
        return
    
    order_id = context.bot_data['bkash_orders'][user_id]
    
    if not re.match(r'^[A-Z0-9]{8,15}$', text.upper()):
        await update.message.reply_text(
            "❌ Invalid Transaction ID format!\n\n"
            "Please enter a valid TRX ID from your bKash SMS.\n"
            "Example: `CKL9EU0SPR`",
            parse_mode='Markdown'
        )
        return
    
    txn_id = text.upper()
    
    existing = await db.fetchrow(
        "SELECT order_id FROM orders WHERE bkash_txn_id = $1",
        txn_id
    )
    
    if existing:
        await update.message.reply_text(
            "❌ This Transaction ID has already been used!\n\n"
            "Please check your TRX ID or contact support.",
            parse_mode='Markdown'
        )
        return
    
    await db.execute(
        "UPDATE orders SET bkash_txn_id = $1 WHERE order_id = $2",
        txn_id, order_id
    )
    
    del context.bot_data['bkash_orders'][user_id]
    
    await update.message.reply_text(
        f"✅ **Transaction ID Received!**\n\n"
        f"🆔 TRX ID: `{txn_id}`\n"
        f"🆔 Order: `{order_id}`\n\n"
        f"⏳ Your payment is now **under processing**...\n\n"
        f"We're verifying your bKash payment. This usually takes 1-5 minutes.\n"
        f"You'll receive your key automatically once verified! 🚀",
        parse_mode='Markdown'
    )
    
    logger.info(f"📱 User {user_id} submitted TRX ID: {txn_id} for order: {order_id}")


# 💰 PAYMENT PROCESSING - AUTO KEY DELIVERY
# ═══════════════════════════════════════════════════════════

async def process_successful_payment(context: ContextTypes.DEFAULT_TYPE, order_id: str, utr: str = ""):
    """Process payment and deliver key AUTOMATICALLY"""
    
    order = await db.fetchrow("SELECT * FROM orders WHERE order_id = $1 AND status = 'pending'", order_id)
    
    if not order:
        return
    
    # Mark as success
    await db.execute("UPDATE orders SET status = 'success', utr = $1, completed_at = CURRENT_TIMESTAMP WHERE order_id = $2", utr, order_id)
    
    # Get variant details
    variant = await db.fetchrow("""
        SELECT v.*, p.name as product_name, p.emoji, p.group_link
        FROM variants v
        JOIN products p ON p.id = v.product_id
        WHERE v.id = $1
    """, order['variant_id'])
    
    # Get key
    key_data = await get_next_available_key(order['variant_id'], order['user_id'])
    
    if key_data:
        # Save sale
        await db.execute("""
            INSERT INTO sales (order_id, user_id, variant_id, product_name, variant_name, amount, key_delivered)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        """, order_id, order['user_id'], order['variant_id'], variant['product_name'], variant['name'], order['amount'], key_data)
        
        # Send key to user
        msg = (
            f"\n"
            f"   ✅ *PAYMENT SUCCESS!*   \n"
            f"\n\n"
            f"🎉 *Your order is complete!*\n"
            f"━━━━━━━━━━━━━━━━━━━\n\n"
            f"🛒 *ORDER DETAILS:*\n"
            f"├ {variant['emoji']} Product: *{variant['product_name']}*\n"
            f"├ ⏱️ Duration: *{variant['name']}*\n"
            f"├ 💰 Amount Paid: *₹{int(order['amount'])}*\n"
            f"└ 📝 Order ID: `{order_id}`\n"
        )
        
        if utr:
            msg += f"└ 🔢 UTR: `{utr}`\n"
        
        msg += (
            f"\n━━━━━━━━━━━━━━━━━━━\n"
            f"🔑 *YOUR LICENSE KEY:*\n\n"
            f"`{key_data}`\n\n"
        )
        
        # ADD GROUP LINK if product has one
        if variant.get('group_link'):
            msg += (
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"👥 *JOIN OUR GROUP:*\n\n"
                f"{variant['group_link']}\n\n"
            )
        
        msg += (
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ *IMPORTANT:*\n"
            f"• Keep this key safe and secure\n"
            f"• Don't share with others\n"
            f"• Check 🛍️ My Orders anytime\n\n"
            f"💎 *Thank you for shopping with us!*\n"
            f"🎮 *Enjoy your game!*"
        )
        
        try:
            await context.bot.send_message(chat_id=order['user_id'], text=msg, parse_mode="Markdown")
            
            # Delete QR
            if order['qr_message_id']:
                try:
                    await context.bot.delete_message(chat_id=order['chat_id'], message_id=order['qr_message_id'])
                except:
                    try:
                        await context.bot.edit_message_text(
                            chat_id=order['chat_id'],
                            message_id=order['qr_message_id'],
                            text="✅ *Paid!* Check your DM for the key. 🔑",
                            parse_mode="Markdown"
                        )
                    except:
                        pass
        except Exception as e:
            logger.error(f"Key send failed: {e}")
        
        # Notify admin (convert INR to USD for admin)
        usd_amount = order['amount'] / USD_TO_INR
        
        # Get user FULL info (name + username)
        user_info = await db.fetchrow("SELECT first_name, username FROM users WHERE user_id = $1", order['user_id'])
        full_name = user_info['first_name'] if user_info else f"User{order['user_id']}"
        username = f"@{user_info['username']}" if user_info and user_info['username'] else "No username"
        
        # Get STOCK REMAINING after this sale
        stock_remaining = await db.fetchval(
            "SELECT COUNT(*) FROM keys WHERE variant_id = $1 AND status = 'available'",
            order['variant_id']
        ) or 0
        
        # Bangladesh time (AM/PM format)
        time_now_bd = datetime.utcnow() + timedelta(hours=6)
        time_str = time_now_bd.strftime("%I:%M %p")
        timestamp_str = time_now_bd.strftime("%Y-%m-%d %I:%M:%S %p")
        
        # PROFESSIONAL notification with FULL details
        admin_msg = (
            f"⚠️ *Purchase Success Notification*\n\n"
            f"👤 *Customer:* {full_name}\n"
            f"👤 *Username:* {username}\n"
            f"🔗 *Type:* Regular\n"
            f"🆔 *User ID:* `{order['user_id']}`\n\n"
            f"{variant['emoji']} *Game:* {variant['product_name']}\n"
            f"⏳ *Duration:* {variant['name']}\n"
            f"💰 *Amount:* ${usd_amount:.2f} (₹{order['amount']:.0f})\n"
            f"📝 *Order ID:* `{order_id}`\n\n"
        )
        
        # LOW STOCK WARNING if needed
        if stock_remaining <= 5:
            admin_msg += f"⚠️ *Low Stock Warning!*\n"
        
        admin_msg += (
            f"{variant['emoji']} *Game:* {variant['product_name']}\n"
            f"⏳ *Duration:* {variant['name']}\n"
            f"📦 *Stock Remaining:* {stock_remaining} keys\n"
            f"📅 *Timestamp:* {timestamp_str}\n"
        )
        
        await notify_admin(context, admin_msg)
        
    else:
        # No keys available
        msg = f"✅ *Payment Received!*\n\n📝 `{order_id}`\n💰 ₹{int(order['amount'])}\n\n⚠️ *Temporarily out of stock*\n\n🔑 Your key will be delivered within 1 hour.\n\nThank you for your patience! 🙏"
        
        try:
            await context.bot.send_message(chat_id=order['user_id'], text=msg, parse_mode="Markdown")
        except:
            pass
        
        # Alert admin (convert INR back to USD for admin)
        usd_amount = order['amount'] / USD_TO_INR
        await notify_admin(context, f"🚨 *URGENT: NO KEYS AVAILABLE!*\n\nOrder `{order_id}` PAID BUT NO KEYS!\nUser: `{order['user_id']}`\n{variant['product_name']} ├ {variant['name']}\n💵 ${usd_amount:.2f} (₹{order['amount']:.0f})\n\n⚠️ *ADD KEYS IMMEDIATELY!*")

# ═══════════════════════════════════════════════════════════
# 👮 ADMIN PANEL - 100% BUTTON BASED!
# ═══════════════════════════════════════════════════════════

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main admin panel - ALL BUTTONS"""
    if update.effective_user.id not in ADMIN_IDS:
        return
    
    # Stats - More detailed
    total_users = await db.fetchval("SELECT COUNT(*) FROM users")
    total_sales = await db.fetchval("SELECT COUNT(*) FROM orders WHERE status = 'success'")
    
    # Revenue calculation - SEPARATE by payment method!
    # UPI orders: amount is in INR
    revenue_inr = await db.fetchval("""
        SELECT COALESCE(SUM(amount), 0) FROM orders 
        WHERE status = 'success' AND payment_method = 'upi'
    """) or 0
    
    # Crypto orders: amount is in USD
    revenue_usdt = await db.fetchval("""
        SELECT COALESCE(SUM(amount), 0) FROM orders 
        WHERE status = 'success' AND payment_method = 'crypto'
    """) or 0
    
    pending = await db.fetchval("SELECT COUNT(*) FROM orders WHERE status = 'pending'")
    
    # Today's stats
    today_sales = await db.fetchval("""
        SELECT COUNT(*) FROM orders 
        WHERE status = 'success' AND DATE(completed_at) = CURRENT_DATE
    """) or 0
    
    # Today's revenue - SEPARATE by payment method!
    today_revenue_inr = await db.fetchval("""
        SELECT COALESCE(SUM(amount), 0) FROM orders 
        WHERE status = 'success' AND DATE(completed_at) = CURRENT_DATE AND payment_method = 'upi'
    """) or 0
    
    today_revenue_usdt = await db.fetchval("""
        SELECT COALESCE(SUM(amount), 0) FROM orders 
        WHERE status = 'success' AND DATE(completed_at) = CURRENT_DATE AND payment_method = 'crypto'
    """) or 0
    
    text = (
        f"*🔐 ADMIN CONTROL PANEL*\n\n"
        f"📊 *Quick Stats:*\n"
        f"├ 👥 Total Users: *{total_users}*\n"
        f"├ 💰 Total Sales: *{total_sales}*\n"
        f"├ 💵 UPI Revenue: *₹{revenue_inr:.0f}*\n"
        f"├ 💎 Crypto Revenue: *${revenue_usdt:.2f} USDT*\n"
        f"├ 📈 Today's Sales: *{today_sales}*\n"
        f"├ 🇮🇳 Today UPI: ₹{today_revenue_inr:.0f}\n"
        f"├ 💎 Today Crypto: ${today_revenue_usdt:.2f} USDT\n"
        f"└ ⏳ Pending Orders: *{pending}*\n\n"
        f"🎛️ *Control Panel:*"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("📦 Products", callback_data="admin_products"),
            InlineKeyboardButton("🔑 Keys", callback_data="admin_keys")
        ],
        [
            InlineKeyboardButton("📜 Order History", callback_data="admin_order_history"),
            InlineKeyboardButton("🔍 Search Order", callback_data="admin_search_order")
        ],
        [
            InlineKeyboardButton("📈 Analytics", callback_data="admin_analytics"),
            InlineKeyboardButton("👥 Users", callback_data="admin_users")
        ],
        [
            InlineKeyboardButton("💎 Custom Pricing", callback_data="admin_custom_pricing"),
            InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast_menu")
        ],
        [InlineKeyboardButton("🛒 View Shop", callback_data="start_menu"),
         InlineKeyboardButton("❌ Close", callback_data="admin_exit")]
    ]
    
    markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")
        await update.callback_query.answer()
    else:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")

async def admin_exit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Close admin panel"""
    query = update.callback_query
    await query.edit_message_text("👋 *Admin panel closed.*\n\n💡 Send /start anytime to reopen!", parse_mode="Markdown")
    await query.answer()

# ═══════════════════════════════════════════════════════════
# 📜 ORDER HISTORY - NEW FEATURE!
# ═══════════════════════════════════════════════════════════

async def admin_order_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View recent order history"""
    query = update.callback_query
    
    # Get recent orders
    orders = await db.fetch("""
        SELECT o.*, u.first_name, u.username
        FROM orders o
        LEFT JOIN users u ON u.user_id = o.user_id
        WHERE o.status = 'success'
        ORDER BY o.completed_at DESC
        LIMIT 20
    """)
    
    if not orders:
        text = (
            f"*📜 ORDER HISTORY*\n\n"
            f"❌ No completed orders yet."
        )
    else:
        text = (
            f"*📜 ORDER HISTORY*\n\n"
            f"*Recent Orders (Last 20):*\n\n"
        )
        
        for i, order in enumerate(orders, 1):
            user_name = order['first_name'] or order['username'] or f"User{order['user_id']}"
            completed = order['completed_at'].strftime("%m/%d %H:%M") if order['completed_at'] else "N/A"
            
            text += (
                f"*{i}. {completed}*\n"
                f"├ 👤 {user_name} (`{order['user_id']}`)\n"
                f"├ 💰 Amount: ₹{order['amount']:.0f}\n"
                f"├ 📝 Order ID: `{order['order_id']}`\n"
                f"└ 🔢 UTR: `{order['utr'] or 'N/A'}`\n\n"
            )
    
    keyboard = [[InlineKeyboardButton("⬅️ Back to Admin", callback_data="admin_home")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

# ═══════════════════════════════════════════════════════════
# 🔍 SEARCH ORDER - NEW FEATURE!
# ═══════════════════════════════════════════════════════════

async def admin_search_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search order by ID"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    admin_states[user_id] = {'action': 'search_order'}
    
    await query.edit_message_text(
        f"*🔍 SEARCH ORDER*\n\n"
        f"📝 Send the *Order ID* to search:\n\n"
        f"├ 💡 Example: `ORD1731603123456`\n"
        f"└ 📜 Find Order IDs in Order History",
        parse_mode='Markdown'
    )

async def show_order_details(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: str):
    """Show complete order details"""
    
    # Fetch order with all details
    order = await db.fetchrow("""
        SELECT 
            o.order_id,
            o.user_id,
            o.amount,
            o.status,
            o.utr,
            o.created_at,
            o.completed_at,
            u.first_name,
            u.username,
            u.phone_number,
            v.name as variant_name,
            v.validity_days,
            p.name as product_name,
            p.emoji,
            s.key_delivered
        FROM orders o
        LEFT JOIN users u ON o.user_id = u.user_id
        LEFT JOIN variants v ON o.variant_id = v.id
        LEFT JOIN products p ON v.product_id = p.id
        LEFT JOIN sales s ON o.order_id = s.order_id
        WHERE o.order_id = $1
    """, order_id)
    
    if not order:
        text = f"❌ *Order Not Found!*\n\n`{order_id}`\n\nPlease check the Order ID."
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin_home")]]
    else:
        # Format dates in Bangladesh time
        from datetime import timedelta
        created_bd = order['created_at'] + timedelta(hours=6)
        created_str = created_bd.strftime("%d %b %Y, %I:%M %p BST")
        
        completed_str = "Pending"
        if order['completed_at']:
            completed_bd = order['completed_at'] + timedelta(hours=6)
            completed_str = completed_bd.strftime("%d %b %Y, %I:%M %p BST")
        
        status_emoji = {'success': '✅', 'pending': '⏳', 'expired': '⏰', 'failed': '❌'}.get(order['status'], '❓')
        
        # User info
        user_name = order['first_name'] or order['username'] or f"User{order['user_id']}"
        username = f"@{order['username']}" if order['username'] else "No username"
        phone = order['phone_number'] or "Not provided"
        
        # Product info
        emoji = order['emoji'] or '🎮'
        product = order['product_name'] or "Unknown"
        variant = order['variant_name'] or "Unknown"
        validity = f"{order['validity_days']} Days" if order['validity_days'] and order['validity_days'] > 0 else "Lifetime"
        
        # Convert INR to USD for admin view
        amount_inr = int(order['amount'])
        amount_usd = order['amount'] / USD_TO_INR
        
        text = (
            f"{status_emoji} *ORDER DETAILS* {status_emoji}\n"
            f"━━━━━━━━━━━━━━━━━━━\n\n"
            f"📝 *Order ID:* `{order['order_id']}`\n"
            f"📊 *Status:* {order['status'].upper()}\n"
            f"💰 *Amount:* ₹{amount_inr} (${amount_usd:.2f})\n"
            f"💳 *UTR:* `{order['utr'] or 'N/A'}`\n\n"
            f"👤 *CUSTOMER INFO:*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"• Name: {user_name}\n"
            f"• Username: {username}\n"
            f"• User ID: `{order['user_id']}`\n"
            f"• Phone: {phone}\n\n"
            f"📦 *PRODUCT INFO:*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"• Product: {emoji} {product}\n"
            f"• Plan: {variant}\n"
            f"• Validity: {validity}\n\n"
            f"📅 *TIMELINE:*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"• Created: {created_str}\n"
            f"• Completed: {completed_str}\n"
        )
        
        if order['status'] == 'success' and order['key_delivered']:
            text += f"\n🔑 *LICENSE KEY:*\n`{order['key_delivered']}`\n"
        
        keyboard = [
            [InlineKeyboardButton("🔍 Search Another", callback_data="admin_search_order")],
            [InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_home")]
        ]
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# ═══════════════════════════════════════════════════════════
# 📈 ANALYTICS - NEW FEATURE!
# ═══════════════════════════════════════════════════════════

async def admin_analytics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View detailed analytics"""
    query = update.callback_query
    
    # Daily sales
    daily_sales = await db.fetchval("""
        SELECT COUNT(*) FROM orders 
        WHERE status = 'success' AND DATE(completed_at) = CURRENT_DATE
    """) or 0
    
    # Daily revenue - SEPARATE by payment method!
    daily_revenue_inr = await db.fetchval("""
        SELECT COALESCE(SUM(amount), 0) FROM orders 
        WHERE status = 'success' AND DATE(completed_at) = CURRENT_DATE AND payment_method = 'upi'
    """) or 0
    
    daily_revenue_usdt = await db.fetchval("""
        SELECT COALESCE(SUM(amount), 0) FROM orders 
        WHERE status = 'success' AND DATE(completed_at) = CURRENT_DATE AND payment_method = 'crypto'
    """) or 0
    
    # Monthly sales
    monthly_sales = await db.fetchval("""
        SELECT COUNT(*) FROM orders 
        WHERE status = 'success' AND DATE_TRUNC('month', completed_at) = DATE_TRUNC('month', CURRENT_DATE)
    """) or 0
    
    # Monthly revenue - SEPARATE by payment method!
    monthly_revenue_inr = await db.fetchval("""
        SELECT COALESCE(SUM(amount), 0) FROM orders 
        WHERE status = 'success' AND DATE_TRUNC('month', completed_at) = DATE_TRUNC('month', CURRENT_DATE) AND payment_method = 'upi'
    """) or 0
    
    monthly_revenue_usdt = await db.fetchval("""
        SELECT COALESCE(SUM(amount), 0) FROM orders 
        WHERE status = 'success' AND DATE_TRUNC('month', completed_at) = DATE_TRUNC('month', CURRENT_DATE) AND payment_method = 'crypto'
    """) or 0
    
    # Top 5 purchased products
    top_products = await db.fetch("""
        SELECT s.product_name, s.variant_name, COUNT(*) as sales, SUM(s.amount) as revenue
        FROM sales s
        GROUP BY s.product_name, s.variant_name
        ORDER BY sales DESC
        LIMIT 5
    """)
    
    text = (
        f"*📈 SALES ANALYTICS*\n\n"
        f"*📊 Today's Performance:*\n"
        f"├ 💰 Sales Count: *{daily_sales}*\n"
        f"├ 🇮🇳 UPI Revenue: ₹{daily_revenue_inr:.0f}\n"
        f"└ 💎 Crypto Revenue: ${daily_revenue_usdt:.2f} USDT\n\n"
        f"*📅 This Month:*\n"
        f"├ 💰 Sales Count: *{monthly_sales}*\n"
        f"├ 🇮🇳 UPI Revenue: ₹{monthly_revenue_inr:.0f}\n"
        f"└ 💎 Crypto Revenue: ${monthly_revenue_usdt:.2f} USDT\n\n"
    )
    
    if top_products:
        text += f"*🏆 Top 5 Products:*\n"
        for i, prod in enumerate(top_products, 1):
            text += f"├ {i}. *{prod['product_name']}* • {prod['variant_name']}\n   ├ 💰 Sales: {prod['sales']}\n   └ 💵 Revenue: ₹{prod['revenue']:.0f}\n"
    else:
        text += "*🏆 Top Products:*\n└ No sales data yet."
    
    keyboard = [[InlineKeyboardButton("⬅️ Back to Admin", callback_data="admin_home")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

# ═══════════════════════════════════════════════════════════
# 🎁 RESELLER MANAGEMENT - NEW FEATURE!
# ═══════════════════════════════════════════════════════════

async def admin_resellers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manage resellers"""
    query = update.callback_query
    
    # Clear any waiting states to prevent bugs
    if 'waiting_for' in context.user_data:
        context.user_data.clear()
    
    resellers = await db.fetch("""
        SELECT r.*, u.first_name, u.username
        FROM resellers r
        LEFT JOIN users u ON u.user_id = r.user_id
        WHERE r.is_active = TRUE
        ORDER BY r.discount_percent DESC
    """)
    
    text = f"*🎁 Reseller Management*\n━━━━━━━━━━━━━━━━━━━\n\n"
    
    if resellers:
        text += f"*Active Resellers: {len(resellers)}*\n\n"
        for res in resellers:
            user_name = res['first_name'] or res['username'] or f"User{res['user_id']}"
            text += f"👤 *{user_name}*\n├ ID: `{res['user_id']}`\n├ Discount: *{res['discount_percent']}%*\n└ Note: {res['notes'] or 'None'}\n\n"
    else:
        text += "❌ No active resellers\n\n"
    
    text += "*💡 Click below to add reseller via buttons!*"
    
    keyboard = [
        [InlineKeyboardButton("➕ Add Reseller", callback_data="admin_add_reseller_step1")],
        [InlineKeyboardButton("🗑️ Remove Reseller", callback_data="admin_remove_reseller")],
        [InlineKeyboardButton("⬅️ Back to Admin", callback_data="admin_home")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def admin_add_reseller_step1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 1: Select discount percentage"""
    query = update.callback_query
    
    text = (
        "*🎁 Add Reseller*\n━━━━━━━━━━━━━━━━━━━\n\n"
        "*Step 1: Select Discount Percentage*\n\n"
        "Choose discount % for reseller:"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("5%", callback_data="admin_reseller_disc_5"),
            InlineKeyboardButton("10%", callback_data="admin_reseller_disc_10"),
            InlineKeyboardButton("15%", callback_data="admin_reseller_disc_15")
        ],
        [
            InlineKeyboardButton("20%", callback_data="admin_reseller_disc_20"),
            InlineKeyboardButton("25%", callback_data="admin_reseller_disc_25"),
            InlineKeyboardButton("30%", callback_data="admin_reseller_disc_30")
        ],
        [
            InlineKeyboardButton("35%", callback_data="admin_reseller_disc_35"),
            InlineKeyboardButton("40%", callback_data="admin_reseller_disc_40"),
            InlineKeyboardButton("50%", callback_data="admin_reseller_disc_50")
        ],
        [InlineKeyboardButton("✏️ Custom %", callback_data="admin_reseller_disc_custom")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_resellers")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def admin_add_reseller_step2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 2: Enter chat ID"""
    query = update.callback_query
    
    # Get discount from callback
    if query.data == "admin_reseller_disc_custom":
        text = (
            "*🎁 Add Reseller*\n━━━━━━━━━━━━━━━━━━━\n\n"
            "*Step 2: Enter Custom Discount %*\n\n"
            "Reply with discount percentage:\n"
            "Example: `12` for 12%\n\n"
            "⚠️ Reply with just the number (1-99)!"
        )
        context.user_data['waiting_for'] = 'reseller_discount'
        
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="admin_resellers")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        await query.answer()
        return
    
    # Extract discount percentage from callback
    discount = int(query.data.split("_")[-1])
    context.user_data['reseller_discount'] = discount
    
    text = (
        f"*🎁 Add Reseller*\n━━━━━━━━━━━━━━━━━━━\n\n"
        f"*Step 3: Enter User Chat ID*\n\n"
        f"Discount: *{discount}%*\n\n"
        f"*Reply with user's Chat ID:*\n"
        f"Example: `123456789`\n\n"
        f"💡 Get Chat ID from Order History or ask user to message @userinfobot"
    )
    
    context.user_data['waiting_for'] = 'reseller_chatid'
    
    keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="admin_resellers")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def admin_remove_reseller(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove reseller - list all active resellers"""
    query = update.callback_query
    
    resellers = await db.fetch("""
        SELECT r.*, u.first_name, u.username
        FROM resellers r
        LEFT JOIN users u ON u.user_id = r.user_id
        WHERE r.is_active = TRUE
        ORDER BY r.discount_percent DESC
    """)
    
    if not resellers:
        await query.answer("❌ No active resellers to remove!", show_alert=True)
        return
    
    text = f"*🗑️ Remove Reseller*\n━━━━━━━━━━━━━━━━━━━\n\n*Select reseller to remove:*\n\n"
    
    keyboard = []
    for res in resellers:
        user_name = res['first_name'] or res['username'] or f"User{res['user_id']}"
        keyboard.append([InlineKeyboardButton(
            f"❌ {user_name} ({res['discount_percent']}%)",
            callback_data=f"admin_do_remove_reseller_{res['user_id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_resellers")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def admin_do_remove_reseller(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Actually remove the reseller"""
    query = update.callback_query
    user_id = int(query.data.split("_")[-1])
    
    # Get user info before removing
    user_info = await db.fetchrow("""
        SELECT u.first_name, u.username, r.discount_percent
        FROM resellers r
        LEFT JOIN users u ON u.user_id = r.user_id
        WHERE r.user_id = $1
    """, user_id)
    
    # Deactivate reseller (soft delete)
    await db.execute("""
        UPDATE resellers 
        SET is_active = FALSE 
        WHERE user_id = $1
    """, user_id)
    
    user_name = user_info['first_name'] or user_info['username'] or f"User{user_id}"
    
    await query.answer("✅ Reseller removed!", show_alert=True)
    
    # Show updated reseller list
    await admin_resellers(update, context)

# ═══════════════════════════════════════════════════════════
# 💎 CUSTOM PRICING - NEW FEATURE!
# ═══════════════════════════════════════════════════════════

async def admin_custom_pricing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manage custom pricing"""
    query = update.callback_query
    
    # Clear any waiting states to prevent bugs
    if 'waiting_for' in context.user_data:
        context.user_data.clear()
    
    custom_prices = await db.fetch("""
        SELECT cp.*, u.first_name, u.username, v.name as variant_name, p.name as product_name
        FROM custom_pricing cp
        LEFT JOIN users u ON u.user_id = cp.user_id
        LEFT JOIN variants v ON v.id = cp.variant_id
        LEFT JOIN products p ON p.id = v.product_id
        WHERE cp.is_active = TRUE
        ORDER BY cp.created_at DESC
        LIMIT 10
    """)
    
    text = f"*💎 CUSTOM PRICING*\n\n"
    
    if custom_prices:
        text += f"*Active Custom Prices: {len(custom_prices)}*\n\n"
        for cp in custom_prices:
            user_name = cp['first_name'] or cp['username'] or f"User{cp['user_id']}"
            inr_price = int(cp['custom_price'] * USD_TO_INR)
            text += (
                f"👤 *{user_name}* (`{cp['user_id']}`)\n"
                f"├ Product: {cp['product_name']} ├ {cp['variant_name']}\n"
                f"└ Price: *${cp['custom_price']:.2f}* (₹{inr_price})\n\n"
            )
    else:
        text += "❌ No custom pricing set\n\n"
    
    text += "└ 🎯 *Choose an action below:*"
    
    keyboard = [
        [InlineKeyboardButton("➕ Set Custom Price", callback_data="admin_set_custom_price_step1")],
        [InlineKeyboardButton("🗑️ Remove Custom Price", callback_data="admin_remove_custom_price")],
        [InlineKeyboardButton("⬅️ Back to Admin", callback_data="admin_home")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def admin_set_custom_price_step1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 1: Select product for custom pricing"""
    query = update.callback_query
    
    products = await db.fetch("SELECT * FROM products WHERE is_active = TRUE ORDER BY name")
    
    if not products:
        await query.answer("❌ No products available!", show_alert=True)
        return
    
    text = "*💎 Set Custom Price*\n━━━━━━━━━━━━━━━━━━━\n\n*Step 1: Select Product*"
    
    keyboard = []
    for prod in products:
        keyboard.append([InlineKeyboardButton(
            f"{prod['emoji']} {prod['name']}", 
            callback_data=f"admin_custprice_prod_{prod['id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_custom_pricing")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def admin_set_custom_price_step2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 2: Select variant for custom pricing"""
    query = update.callback_query
    product_id = int(query.data.split("_")[-1])
    
    product = await db.fetchrow("SELECT * FROM products WHERE id = $1", product_id)
    variants = await db.fetch("SELECT * FROM variants WHERE product_id = $1 AND is_active = TRUE ORDER BY validity_days", product_id)
    
    if not variants:
        await query.answer("❌ No variants available!", show_alert=True)
        return
    
    text = f"*💎 Set Custom Price*\n━━━━━━━━━━━━━━━━━━━\n\n*Step 2: Select Variant*\n{product['emoji']} {product['name']}"
    
    keyboard = []
    for var in variants:
        usd_price = float(var['price'])
        inr_price = int(usd_price * USD_TO_INR)
        keyboard.append([InlineKeyboardButton(
            f"⏱ {var['name']} (${usd_price:.2f} = ₹{inr_price})", 
            callback_data=f"admin_custprice_var_{var['id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_set_custom_price_step1")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def admin_set_custom_price_step3(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 3: Select price amount"""
    query = update.callback_query
    variant_id = int(query.data.split("_")[-1])
    
    variant = await db.fetchrow("""
        SELECT v.*, p.name as product_name, p.emoji
        FROM variants v
        JOIN products p ON p.id = v.product_id
        WHERE v.id = $1
    """, variant_id)
    
    base_price_usd = float(variant['price'])
    base_price_inr = int(base_price_usd * USD_TO_INR)
    
    text = (
        f"*💎 Set Custom Price*\n━━━━━━━━━━━━━━━━━━━\n\n"
        f"*Step 3: Select Custom Price (USD)*\n\n"
        f"{variant['emoji']} {variant['product_name']} ├ {variant['name']}\n"
        f"Base Price: *${base_price_usd:.2f}* (₹{base_price_inr})\n\n"
        f"*Choose custom price in USD:*"
    )
    
    # Store variant_id in user data for next step
    context.user_data['custom_price_variant_id'] = variant_id
    
    # Preset price options IN USD (database stores USD!)
    keyboard = []
    
    # Common USD price options
    keyboard.append([
        InlineKeyboardButton("$0.50", callback_data="admin_custprice_amt_0.50"),
        InlineKeyboardButton("$1.00", callback_data="admin_custprice_amt_1.00"),
        InlineKeyboardButton("$1.50", callback_data="admin_custprice_amt_1.50")
    ])
    keyboard.append([
        InlineKeyboardButton("$2.00", callback_data="admin_custprice_amt_2.00"),
        InlineKeyboardButton("$2.50", callback_data="admin_custprice_amt_2.50"),
        InlineKeyboardButton("$3.00", callback_data="admin_custprice_amt_3.00")
    ])
    keyboard.append([
        InlineKeyboardButton("$5.00", callback_data="admin_custprice_amt_5.00"),
        InlineKeyboardButton("$10.00", callback_data="admin_custprice_amt_10.00"),
        InlineKeyboardButton("$15.00", callback_data="admin_custprice_amt_15.00")
    ])
    
    keyboard.append([InlineKeyboardButton("✏️ Enter Custom Amount (USD)", callback_data="admin_custprice_amt_custom")])
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data=f"admin_custprice_prod_{variant['product_id']}")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def admin_set_custom_price_step4(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 4: Enter chat ID"""
    query = update.callback_query
    
    # Get price from callback
    if query.data == "admin_custprice_amt_custom":
        # Will handle custom amount entry via text
        text = (
            "*💎 Set Custom Price*\n━━━━━━━━━━━━━━━━━━━\n\n"
            "*Step 4: Enter Custom Amount in USD*\n\n"
            "Reply with amount in $ (USD):\n"
            "Example: `2.50` for $2.50\n\n"
            "⚠️ Reply with just the number (decimals allowed)!"
        )
        context.user_data['waiting_for'] = 'custom_price_amount'
        
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="admin_custom_pricing")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        await query.answer()
        return
    
    # Extract USD amount from callback (buttons now send USD amounts!)
    usd_amount = float(query.data.split("_")[-1])
    
    context.user_data['custom_price_usd'] = usd_amount
    
    variant_id = context.user_data.get('custom_price_variant_id')
    variant = await db.fetchrow("""
        SELECT v.*, p.name as product_name, p.emoji
        FROM variants v
        JOIN products p ON p.id = v.product_id
        WHERE v.id = $1
    """, variant_id)
    
    inr_amount = int(usd_amount * USD_TO_INR)
    
    text = (
        f"*💎 Set Custom Price*\n━━━━━━━━━━━━━━━━━━━\n\n"
        f"*Step 5: Enter User Chat ID*\n\n"
        f"{variant['emoji']} {variant['product_name']} ├ {variant['name']}\n"
        f"Custom Price: *${usd_amount:.2f}* (₹{inr_amount})\n\n"
        f"*Reply with user's Chat ID:*\n"
        f"Example: `123456789`\n\n"
        f"💡 Get Chat ID from Order History or ask user to message @userinfobot"
    )
    
    context.user_data['waiting_for'] = 'custom_price_chatid'
    
    keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="admin_custom_pricing")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def admin_remove_custom_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove custom price - list all active custom prices"""
    query = update.callback_query
    
    custom_prices = await db.fetch("""
        SELECT cp.*, u.first_name, u.username, v.name as variant_name, p.name as product_name
        FROM custom_pricing cp
        LEFT JOIN users u ON u.user_id = cp.user_id
        LEFT JOIN variants v ON v.id = cp.variant_id
        LEFT JOIN products p ON p.id = v.product_id
        WHERE cp.is_active = TRUE
        ORDER BY cp.created_at DESC
    """)
    
    if not custom_prices:
        await query.answer("❌ No active custom prices to remove!", show_alert=True)
        return
    
    text = f"*🗑️ Remove Custom Price*\n━━━━━━━━━━━━━━━━━━━\n\n*Select custom price to remove:*\n\n"
    
    keyboard = []
    for cp in custom_prices:
        user_name = cp['first_name'] or cp['username'] or f"User{cp['user_id']}"
        usd_price = float(cp['custom_price'])
        inr_price = int(usd_price * USD_TO_INR)
        
        button_text = f"❌ {user_name} - {cp['product_name']} ({cp['variant_name']}) ${usd_price:.2f}"
        
        keyboard.append([InlineKeyboardButton(
            button_text[:60],  # Truncate if too long
            callback_data=f"admin_do_remove_custprice_{cp['id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_custom_pricing")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def admin_do_remove_custom_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Actually remove the custom price"""
    query = update.callback_query
    cp_id = int(query.data.split("_")[-1])
    
    # Get info before removing
    cp_info = await db.fetchrow("""
        SELECT cp.*, u.first_name, u.username, v.name as variant_name, p.name as product_name
        FROM custom_pricing cp
        LEFT JOIN users u ON u.user_id = cp.user_id
        LEFT JOIN variants v ON v.id = cp.variant_id
        LEFT JOIN products p ON p.id = v.product_id
        WHERE cp.id = $1
    """, cp_id)
    
    # Deactivate custom price (soft delete)
    await db.execute("""
        UPDATE custom_pricing 
        SET is_active = FALSE 
        WHERE id = $1
    """, cp_id)
    
    await query.answer("✅ Custom price removed!", show_alert=True)
    
    # Show updated custom pricing list
    await admin_custom_pricing(update, context)

# ═══════════════════════════════════════════════════════════
# 🔍 ADMIN DEBUG/TEST COMMANDS
# ═══════════════════════════════════════════════════════════

async def admin_test_pricing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test pricing system - /test <user_id> <variant_id>"""
    if update.effective_user.id not in ADMIN_IDS:
        return
    
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "❌ Usage: `/test <user_id> <variant_id>`\n\n"
            "Example: `/test 123456789 5`\n\n"
            "This will show what price that user sees for that variant.",
            parse_mode="Markdown"
        )
        return
    
    try:
        user_id = int(context.args[0])
        variant_id = int(context.args[1])
        
        # Get variant info
        variant = await db.fetchrow("""
            SELECT v.*, p.name as product_name, p.emoji
            FROM variants v
            JOIN products p ON p.id = v.product_id
            WHERE v.id = $1
        """, variant_id)
        
        if not variant:
            await update.message.reply_text(f"❌ Variant ID {variant_id} not found!")
            return
        
        base_price_usd = float(variant['price'])
        base_price_inr = int(base_price_usd * USD_TO_INR)
        
        # Test custom price
        custom_price = await get_custom_price(user_id, variant_id)
        
        # Test reseller discount
        reseller_discount = await get_reseller_discount(user_id)
        
        # Calculate final price
        final_price_usd, price_note = await calculate_final_price(user_id, variant_id, base_price_usd)
        final_price_inr = int(final_price_usd * USD_TO_INR)
        
        # Check if user exists
        user_exists = await db.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        
        # Build report
        text = (
            f"*🔍 Pricing Test Report*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"*Product:* {variant['emoji']} {variant['product_name']} ├ {variant['name']}\n"
            f"*User ID:* `{user_id}`\n"
            f"*Variant ID:* `{variant_id}`\n\n"
            f"*📊 Results:*\n\n"
            f"*Base Price:*\n"
            f"├ USD: ${base_price_usd:.2f}\n"
            f"└ INR: ₹{base_price_inr}\n\n"
        )
        
        if custom_price:
            custom_inr = int(custom_price * USD_TO_INR)
            text += (
                f"*💎 Custom Price:* ✅ SET\n"
                f"├ USD: ${custom_price:.2f}\n"
                f"└ INR: ₹{custom_inr}\n\n"
            )
        else:
            text += f"*💎 Custom Price:* ❌ Not set\n\n"
        
        if reseller_discount > 0:
            text += (
                f"*🎁 Reseller Discount:* ✅ {reseller_discount}%\n\n"
            )
        else:
            text += f"*🎁 Reseller Discount:* ❌ Not set\n\n"
        
        text += (
            f"*💰 Final Price (What user sees):*\n"
            f"├ USD: ${final_price_usd:.2f}\n"
            f"└ INR: ₹{final_price_inr}\n\n"
        )
        
        if price_note:
            text += f"*✨ Applied:* {price_note}\n\n"
        
        # User status
        if user_exists:
            text += f"*👤 User Status:* ✅ Exists in database\n"
            text += f"├ Name: {user_exists['first_name'] or 'N/A'}\n"
            text += f"└ Blocked: {'Yes ❌' if user_exists['is_blocked'] else 'No ✅'}\n\n"
        else:
            text += f"*👤 User Status:* ⚠️ Not in database (user hasn't started bot yet)\n\n"
        
        text += (
            f"*💡 Note:* User must /start the bot first!\n"
            f"Prices update immediately after setting."
        )
        
        await update.message.reply_text(text, parse_mode="Markdown")
        
    except ValueError:
        await update.message.reply_text("❌ Invalid IDs! Use numbers only.")
    except Exception as e:
        logger.error(f"Test pricing error: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

# ═══════════════════════════════════════════════════════════
# 📦 PRODUCTS MANAGEMENT
# ═══════════════════════════════════════════════════════════

async def admin_products_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Products main menu"""
    query = update.callback_query
    
    products = await db.fetch("SELECT * FROM products ORDER BY id")
    
    text = (
        f"*📦 PRODUCTS MANAGEMENT*\n\n"
        f"├ 📊 Total Products: *{len(products)}*\n"
        f"└ 🎯 Choose an action below:\n"
    )
    
    keyboard = [
        [InlineKeyboardButton("➕ Add New Product", callback_data="admin_add_product_step1")],
        [InlineKeyboardButton("📦 Add Variant", callback_data="admin_add_variant_menu")],
        [InlineKeyboardButton("✏️ Edit Product", callback_data="admin_edit_products")],
        [InlineKeyboardButton("🗑 Delete Product", callback_data="admin_delete_product")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_home")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

# Store admin states
admin_states = {}

async def admin_add_product_step1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 1: Ask for product name"""
    query = update.callback_query
    user_id = query.from_user.id
    
    admin_states[user_id] = {'action': 'add_product', 'step': 1}
    
    text = (
        "*➕ ADD NEW PRODUCT - Step 1/4*\n\n"
        "📝 Please send the *product name*:\n\n"
        "└ 💡 Example: `Fortnite`"
    )
    
    await query.edit_message_text(text, parse_mode="Markdown")
    await query.answer()

async def admin_edit_products_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show list of products to edit"""
    query = update.callback_query
    
    products = await db.fetch("SELECT * FROM products ORDER BY id")
    
    if not products:
        await query.answer("❌ No products to edit!", show_alert=True)
        return
    
    text = "*✏️ SELECT PRODUCT TO EDIT:*\n\n"
    
    keyboard = []
    for p in products:
        status = "✅" if p['is_active'] else "❌"
        keyboard.append([InlineKeyboardButton(
            f"{p['emoji']} {p['name']} {status}", 
            callback_data=f"admin_edit_prod_{p['id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_products")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def admin_add_variant_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show list of products to add variant to"""
    query = update.callback_query
    
    products = await db.fetch("SELECT * FROM products ORDER BY id")
    
    if not products:
        await query.answer("❌ No products yet! Add a product first.", show_alert=True)
        return
    
    text = (
        "*📦 ADD VARIANT*\n\n"
        "*Select which product to add variant to:*\n\n"
        "💡 *Tip:* Variants are different price plans\n"
        "(e.g., 1 Day, 7 Days, 1 Month)"
    )
    
    keyboard = []
    for p in products:
        # Show product with emoji
        keyboard.append([InlineKeyboardButton(
            f"{p['emoji']} {p['name']}", 
            callback_data=f"admin_add_variant_step1_{p['id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_products")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def admin_edit_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Edit product menu"""
    query = update.callback_query
    product_id = int(query.data.split("_")[-1])
    
    product = await db.fetchrow("SELECT * FROM products WHERE id = $1", product_id)
    
    if not product:
        await query.answer("❌ Product not found!", show_alert=True)
        return
    
    # Get variants
    variants = await db.fetch("""
        SELECT v.*, COUNT(k.id) FILTER (WHERE k.status = 'available') as stock
        FROM variants v
        LEFT JOIN keys k ON k.variant_id = v.id
        WHERE v.product_id = $1
        GROUP BY v.id
        ORDER BY v.validity_days
    """, product_id)
    
    status = "✅ Active" if product['is_active'] else "❌ Inactive"
    
    text = (
        f"*✏️ EDIT PRODUCT*\n\n"
        f"{product['emoji']} *{product['name']}*\n"
        f"Status: {status}\n\n"
    )
    
    if variants:
        text += "*📊 Variants:*\n"
        for v in variants:
            inr_price = int(v['price'] * USD_TO_INR)
            v_status = "✅" if v['is_active'] else "❌"
            text += f"├ {v['name']} ├ ₹{inr_price} ├ {v['stock']} keys {v_status}\n"
    else:
        text += "⚠️ *No variants yet*\n"
    
    text += "\n*What would you like to do?*"
    
    keyboard = [
        [InlineKeyboardButton("✏️ Edit Product Name", callback_data=f"admin_edit_name_{product_id}")],
        [InlineKeyboardButton("💰 Edit Variant Price", callback_data=f"admin_edit_variant_price_{product_id}")],
        [InlineKeyboardButton("🔗 Edit Group Link", callback_data=f"admin_edit_group_link_{product_id}")],
        [InlineKeyboardButton("🗑 Delete Variant", callback_data=f"admin_delete_variant_{product_id}")],
        [InlineKeyboardButton("🔄 Toggle Active/Inactive", callback_data=f"admin_toggle_product_{product_id}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_edit_products")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def admin_toggle_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle product active status"""
    query = update.callback_query
    product_id = int(query.data.split("_")[-1])
    
    product = await db.fetchrow("SELECT * FROM products WHERE id = $1", product_id)
    
    new_status = not product['is_active']
    await db.execute("UPDATE products SET is_active = $1 WHERE id = $2", new_status, product_id)
    
    status_text = "✅ Active" if new_status else "❌ Inactive"
    await query.answer(f"Product is now {status_text}", show_alert=True)
    
    # Go back to edit menu
    context._callback_query_data = f"admin_edit_prod_{product_id}"
    await admin_edit_product(update, context)

async def admin_edit_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Edit product name - NEW FEATURE"""
    query = update.callback_query
    await query.answer()
    
    product_id = int(query.data.split("_")[-1])
    user_id = query.from_user.id
    
    product = await db.fetchrow("SELECT * FROM products WHERE id = $1", product_id)
    
    if not product:
        await query.answer("❌ Product not found!", show_alert=True)
        return
    
    admin_states[user_id] = {
        'action': 'edit_product_name',
        'product_id': product_id,
        'old_name': product['name']
    }
    
    text = (
        f"*✏️ EDIT PRODUCT NAME*\n\n"
        f"📝 Current Name: *{product['name']}*\n\n"
        f"Send the *new product name*:\n\n"
        f"└ 💡 Example: `Fortnite Premium`"
    )
    
    await query.edit_message_text(text, parse_mode="Markdown")

async def admin_edit_group_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Edit product group link"""
    query = update.callback_query
    await query.answer()
    
    product_id = int(query.data.split("_")[-1])
    user_id = query.from_user.id
    
    # Get current group link
    product = await db.fetchrow("SELECT name, group_link FROM products WHERE id = $1", product_id)
    
    current_link = product['group_link'] if product['group_link'] else "None"
    
    admin_states[user_id] = {
        'action': 'edit_group_link',
        'product_id': product_id,
        'product_name': product['name']
    }
    
    await query.edit_message_text(
        f"*🔗 EDIT GROUP LINK*\n\n"
        f"Product: *{product['name']}*\n"
        f"Current Group: `{current_link}`\n\n"
        f"Send the new *Telegram group/channel link*:\n\n"
        f"Examples:\n"
        f"• https://t.me/yourgroup\n"
        f"• https://t.me/+invite123\n\n"
        f"Or send `-` to remove group link.",
        parse_mode="Markdown"
    )


async def admin_delete_product_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show products to delete"""
    query = update.callback_query
    
    products = await db.fetch("SELECT * FROM products ORDER BY id")
    
    if not products:
        await query.answer("❌ No products to delete!", show_alert=True)
        return
    
    text = "*🗑 SELECT PRODUCT TO DELETE:*\n\n⚠️ *WARNING: This will delete all variants and keys!*\n\n"
    
    keyboard = []
    for p in products:
        keyboard.append([InlineKeyboardButton(
            f"{p['emoji']} {p['name']}", 
            callback_data=f"admin_confirm_delete_prod_{p['id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_products")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def admin_confirm_delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm product deletion"""
    query = update.callback_query
    product_id = int(query.data.split("_")[-1])
    
    product = await db.fetchrow("SELECT * FROM products WHERE id = $1", product_id)
    
    if not product:
        await query.answer("❌ Product not found!", show_alert=True)
        return
    
    # Count variants and keys
    variants_count = await db.fetchval("SELECT COUNT(*) FROM variants WHERE product_id = $1", product_id)
    keys_count = await db.fetchval("""
        SELECT COUNT(*) FROM keys k
        JOIN variants v ON v.id = k.variant_id
        WHERE v.product_id = $1
    """, product_id)
    
    text = (
        f"*🗑 CONFIRM DELETION*\n\n"
        f"⚠️ *Are you absolutely sure?*\n\n"
        f"{product['emoji']} *{product['name']}*\n"
        f"├ Variants: {variants_count}\n"
        f"└ Keys: {keys_count}\n\n"
        f"*This action cannot be undone!*"
    )
    
    keyboard = [
        [InlineKeyboardButton("❌ Yes, Delete Forever", callback_data=f"admin_do_delete_prod_{product_id}")],
        [InlineKeyboardButton("✅ No, Keep It", callback_data="admin_products")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def admin_do_delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Actually delete the product - FIXED TO DELETE ALL VARIANTS AND KEYS"""
    query = update.callback_query
    product_id = int(query.data.split("_")[-1])
    
    product = await db.fetchrow("SELECT * FROM products WHERE id = $1", product_id)
    
    if product:
        try:
            # Get all variant IDs for this product
            variant_ids = await db.fetch("SELECT id FROM variants WHERE product_id = $1", product_id)
            
            # Delete all keys for all variants
            for variant in variant_ids:
                await db.execute("DELETE FROM keys WHERE variant_id = $1", variant['id'])
            
            # Delete all custom pricing for variants
            for variant in variant_ids:
                await db.execute("DELETE FROM custom_pricing WHERE variant_id = $1", variant['id'])
            
            # Delete all variants
            await db.execute("DELETE FROM variants WHERE product_id = $1", product_id)
            
            # Finally delete the product
            await db.execute("DELETE FROM products WHERE id = $1", product_id)
            
            await query.answer(f"✅ {product['name']} and all related data deleted!", show_alert=True)
            logger.info(f"✅ Product {product['name']} (ID: {product_id}) deleted with all variants and keys")
        except Exception as e:
            logger.error(f"❌ Error deleting product: {e}")
            await query.answer("❌ Error deleting product!", show_alert=True)
            return
    
    # Go back to products menu
    await admin_products_menu(update, context)

# ═══════════════════════════════════════════════════════════
# 💰 VARIANT PRICE EDITING - NEW FEATURE!
# ═══════════════════════════════════════════════════════════

async def admin_edit_variant_price_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show variants to edit price"""
    query = update.callback_query
    product_id = int(query.data.split("_")[-1])
    
    variants = await db.fetch("SELECT * FROM variants WHERE product_id = $1 ORDER BY validity_days", product_id)
    
    if not variants:
        await query.answer("❌ No variants to edit!", show_alert=True)
        return
    
    text = "*💰 SELECT VARIANT TO EDIT PRICE:*\n\n"
    
    keyboard = []
    for v in variants:
        inr_price = int(v['price'] * USD_TO_INR)
        keyboard.append([InlineKeyboardButton(
            f"{v['name']} - Current: ₹{inr_price}", 
            callback_data=f"admin_edit_price_var_{v['id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data=f"admin_edit_prod_{product_id}")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def admin_edit_variant_price_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt for new price"""
    query = update.callback_query
    variant_id = int(query.data.split("_")[-1])
    user_id = query.from_user.id
    
    variant = await db.fetchrow("SELECT * FROM variants WHERE id = $1", variant_id)
    
    if not variant:
        await query.answer("❌ Variant not found!", show_alert=True)
        return
    
    admin_states[user_id] = {'action': 'edit_variant_price', 'variant_id': variant_id}
    
    inr_price = int(variant['price'] * USD_TO_INR)
    usd_price = variant['price']
    
    text = (
        f"*💰 EDIT VARIANT PRICE*\n\n"
        f"Variant: *{variant['name']}*\n"
        f"Current Price: *${usd_price:.2f}* (₹{inr_price})\n\n"
        f"Please send the new price in *USD* ($).\n\n"
        f"Examples:\n"
        f"├ `2` for $2.00 (₹180)\n"
        f"├ `5` for $5.00 (₹450)\n"
        f"└ `10` for $10.00 (₹900)\n\n"
        f"💡 *Tip:* Use whole dollars for perfect prices!"
    )
    
    await query.edit_message_text(text, parse_mode="Markdown")
    await query.answer()

# ═══════════════════════════════════════════════════════════
# 🔧 VARIANT MANAGEMENT
# ═══════════════════════════════════════════════════════════

async def admin_add_variant_step1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 1: Ask for variant name"""
    query = update.callback_query
    product_id = int(query.data.split("_")[-1])
    user_id = query.from_user.id
    
    # Get product name to show which product we're adding variant to
    product = await db.fetchrow("SELECT name, emoji FROM products WHERE id = $1", product_id)
    
    admin_states[user_id] = {
        'action': 'add_variant', 
        'step': 1, 
        'product_id': product_id,
        'product_name': product['name'],
        'product_emoji': product['emoji']
    }
    
    text = (
        f"*➕ ADD VARIANT - Step 1/3*\n\n"
        f"*Product:* {product['emoji']} *{product['name']}*\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"Please send the *variant name*.\n\n"
        f"*Examples:*\n"
        f"├ `1 Day`\n"
        f"├ `7 Days`\n"
        f"├ `1 Month`\n"
        f"└ `Lifetime`"
    )
    
    await query.edit_message_text(text, parse_mode="Markdown")
    await query.answer()

async def admin_delete_variant_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show variants to delete"""
    query = update.callback_query
    product_id = int(query.data.split("_")[-1])
    
    variants = await db.fetch("""
        SELECT v.*, COUNT(k.id) as key_count
        FROM variants v
        LEFT JOIN keys k ON k.variant_id = v.id
        WHERE v.product_id = $1
        GROUP BY v.id
        ORDER BY v.validity_days
    """, product_id)
    
    if not variants:
        await query.answer("❌ No variants to delete!", show_alert=True)
        return
    
    text = "*🗑 SELECT VARIANT TO DELETE:*\n\n"
    
    keyboard = []
    for v in variants:
        inr_price = int(v['price'] * USD_TO_INR)
        keyboard.append([InlineKeyboardButton(
            f"{v['name']} - ₹{inr_price} ({v['key_count']} keys)", 
            callback_data=f"admin_confirm_delete_var_{v['id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data=f"admin_edit_prod_{product_id}")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def admin_confirm_delete_variant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm variant deletion"""
    query = update.callback_query
    variant_id = int(query.data.split("_")[-1])
    
    variant = await db.fetchrow("""
        SELECT v.*, p.name as product_name
        FROM variants v
        JOIN products p ON p.id = v.product_id
        WHERE v.id = $1
    """, variant_id)
    
    if not variant:
        await query.answer("❌ Variant not found!", show_alert=True)
        return
    
    keys_count = await db.fetchval("SELECT COUNT(*) FROM keys WHERE variant_id = $1", variant_id)
    inr_price = int(variant['price'] * USD_TO_INR)
    
    text = (
        f"*🗑 CONFIRM DELETION*\n\n"
        f"⚠️ *Are you sure?*\n\n"
        f"Product: {variant['product_name']}\n"
        f"Variant: *{variant['name']}*\n"
        f"Price: ₹{inr_price}\n"
        f"Keys: {keys_count}\n\n"
        f"*This will delete all keys!*"
    )
    
    keyboard = [
        [InlineKeyboardButton("❌ Yes, Delete", callback_data=f"admin_do_delete_var_{variant_id}")],
        [InlineKeyboardButton("✅ No, Keep It", callback_data=f"admin_delete_variant_{variant['product_id']}")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def admin_do_delete_variant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Actually delete the variant"""
    query = update.callback_query
    variant_id = int(query.data.split("_")[-1])
    
    variant = await db.fetchrow("SELECT * FROM variants WHERE id = $1", variant_id)
    
    if variant:
        product_id = variant['product_id']
        await db.execute("DELETE FROM variants WHERE id = $1", variant_id)
        await query.answer(f"✅ Variant deleted!", show_alert=True)
        
        # Go back to product edit
        context._callback_query_data = f"admin_edit_prod_{product_id}"
        await admin_edit_product(update, context)
    else:
        await query.answer("❌ Variant not found!", show_alert=True)

# ═══════════════════════════════════════════════════════════
# 🔑 KEYS MANAGEMENT
# ═══════════════════════════════════════════════════════════

async def admin_keys_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Keys management main menu"""
    query = update.callback_query
    
    total_keys = await db.fetchval("SELECT COUNT(*) FROM keys")
    available = await db.fetchval("SELECT COUNT(*) FROM keys WHERE status = 'available'")
    sold = await db.fetchval("SELECT COUNT(*) FROM keys WHERE status = 'sold'")
    
    text = (
        f"*🔑 KEYS MANAGEMENT*\n\n"
        f"📊 *Statistics:*\n"
        f"├ 🔢 Total Keys: *{total_keys}*\n"
        f"├ ✅ Available: *{available}*\n"
        f"├ 💰 Sold: *{sold}*\n"
        f"└ 🎯 Choose an action below:\n"
    )
    
    keyboard = [
        [InlineKeyboardButton("📤 Upload Keys (Easy)", callback_data="admin_upload_keys_easy")],
        [InlineKeyboardButton("👀 View Keys", callback_data="admin_view_keys")],
        [InlineKeyboardButton("📥 Export Keys", callback_data="admin_export_keys")],
        [InlineKeyboardButton("🗑 Delete Keys", callback_data="admin_delete_keys_menu")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_home")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def admin_upload_keys_easy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Easy upload - select product first"""
    query = update.callback_query
    
    products = await db.fetch("SELECT * FROM products WHERE is_active = TRUE ORDER BY id")
    
    if not products:
        await query.answer("❌ Create a product first!", show_alert=True)
        return
    
    text = "*📤 UPLOAD KEYS - Step 1/2*\n\nSelect the product:"
    
    keyboard = []
    for p in products:
        keyboard.append([InlineKeyboardButton(
            f"{p['emoji']} {p['name']}", 
            callback_data=f"admin_easy_upload_prod_{p['id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_keys")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def admin_easy_upload_variant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Easy upload - select variant"""
    query = update.callback_query
    product_id = int(query.data.split("_")[-1])
    
    variants = await db.fetch("SELECT * FROM variants WHERE product_id = $1 ORDER BY validity_days", product_id)
    
    if not variants:
        await query.answer("❌ Add variants to this product first!", show_alert=True)
        return
    
    text = "*📤 UPLOAD KEYS - Step 2/2*\n\nSelect the variant:"
    
    keyboard = []
    for v in variants:
        inr_price = int(v['price'] * USD_TO_INR)
        keyboard.append([InlineKeyboardButton(
            f"{v['name']} - ₹{inr_price}", 
            callback_data=f"admin_easy_upload_var_{v['id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_upload_keys_easy")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def admin_easy_upload_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt for keys text"""
    query = update.callback_query
    variant_id = int(query.data.split("_")[-1])
    user_id = query.from_user.id
    
    variant = await db.fetchrow("""
        SELECT v.*, p.name as product_name, p.emoji
        FROM variants v
        JOIN products p ON p.id = v.product_id
        WHERE v.id = $1
    """, variant_id)
    
    if not variant:
        await query.answer("❌ Variant not found!", show_alert=True)
        return
    
    admin_states[user_id] = {'action': 'upload_keys', 'variant_id': variant_id}
    
    inr_price = int(variant['price'] * USD_TO_INR)
    
    text = (
        f"*📤 UPLOAD KEYS*\n\n"
        f"{variant['emoji']} *{variant['product_name']}*\n"
        f"Variant: {variant['name']} - ₹{inr_price}\n\n"
        f"Please send your keys, *one per line*.\n\n"
        f"Example:\n"
        f"`KEY-AAAA-1111-BBBB`\n"
        f"`KEY-CCCC-2222-DDDD`\n"
        f"`KEY-EEEE-3333-FFFF`"
    )
    
    await query.edit_message_text(text, parse_mode="Markdown")
    await query.answer()

async def admin_view_keys_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View keys - select product"""
    query = update.callback_query
    
    products = await db.fetch("""
        SELECT DISTINCT p.*
        FROM products p
        JOIN variants v ON v.product_id = p.id
        JOIN keys k ON k.variant_id = v.id
        ORDER BY p.id
    """)
    
    if not products:
        await query.answer("❌ No keys to view!", show_alert=True)
        return
    
    text = "*👀 VIEW KEYS - Step 1/2*\n\nSelect product:"
    
    keyboard = []
    for p in products:
        keyboard.append([InlineKeyboardButton(
            f"{p['emoji']} {p['name']}", 
            callback_data=f"admin_view_keys_prod_{p['id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_keys")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def admin_view_keys_variant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View keys - select variant"""
    query = update.callback_query
    product_id = int(query.data.split("_")[-1])
    
    variants = await db.fetch("""
        SELECT v.*, 
               COUNT(k.id) FILTER (WHERE k.status = 'available') as available,
               COUNT(k.id) FILTER (WHERE k.status = 'sold') as sold
        FROM variants v
        LEFT JOIN keys k ON k.variant_id = v.id
        WHERE v.product_id = $1
        GROUP BY v.id
        HAVING COUNT(k.id) > 0
        ORDER BY v.validity_days
    """, product_id)
    
    if not variants:
        await query.answer("❌ No keys in this product!", show_alert=True)
        return
    
    text = "*👀 VIEW KEYS - Step 2/2*\n\nSelect variant:"
    
    keyboard = []
    for v in variants:
        inr_price = int(v['price'] * USD_TO_INR)
        keyboard.append([InlineKeyboardButton(
            f"{v['name']} - ₹{inr_price} ({v['available']} available)", 
            callback_data=f"admin_show_keys_{v['id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_view_keys")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def admin_show_keys(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Actually show the keys"""
    query = update.callback_query
    variant_id = int(query.data.split("_")[-1])
    
    keys = await db.fetch("""
        SELECT k.*, v.name as variant_name, v.product_id, p.name as product_name, p.emoji
        FROM keys k
        JOIN variants v ON v.id = k.variant_id
        JOIN products p ON p.id = v.product_id
        WHERE k.variant_id = $1
        ORDER BY k.status, k.id
        LIMIT 50
    """, variant_id)
    
    if not keys:
        await query.answer("❌ No keys found!", show_alert=True)
        return
    
    key = keys[0]
    available = len([k for k in keys if k['status'] == 'available'])
    sold = len([k for k in keys if k['status'] == 'sold'])
    
    text = (
        f"*🔑 KEYS LIST*\n\n"
        f"{key['emoji']} *{key['product_name']}*\n"
        f"Variant: {key['variant_name']}\n\n"
        f"📊 Available: {available} | Sold: {sold}\n\n"
        f"*Keys (first 50):*\n\n"
    )
    
    for k in keys[:50]:
        status_emoji = "✅" if k['status'] == 'available' else "❌"
        text += f"{status_emoji} `{k['key_data']}`\n"
    
    keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data=f"admin_view_keys_prod_{key['product_id']}")]]
    
    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    except:
        # Message too long, send as file
        await query.answer("⚠️ Too many keys, sending as file...")
        
        keys_text = "\n".join([f"{k['status'].upper()}: {k['key_data']}" for k in keys])
        file = BytesIO(keys_text.encode('utf-8'))
        file.name = f"keys_{variant_id}.txt"
        
        await context.bot.send_document(
            chat_id=query.message.chat_id,
            document=file,
            caption=f"🔑 Keys for {key['product_name']} - {key['variant_name']}"
        )

async def admin_export_keys(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Export all keys as CSV"""
    query = update.callback_query
    
    await query.answer("📥 Exporting keys...")
    
    keys = await db.fetch("""
        SELECT k.*, v.name as variant_name, v.price, p.name as product_name
        FROM keys k
        JOIN variants v ON v.id = k.variant_id
        JOIN products p ON p.id = v.product_id
        ORDER BY p.name, v.name, k.status, k.id
    """)
    
    if not keys:
        await query.answer("❌ No keys to export!", show_alert=True)
        return
    
    # Create CSV
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['Product', 'Variant', 'Price_USD', 'Key', 'Status', 'Used_By', 'Used_At', 'Added_At'])
    
    for k in keys:
        writer.writerow([
            k['product_name'],
            k['variant_name'],
            k['price'],
            k['key_data'],
            k['status'],
            k['used_by'] or '',
            k['used_at'] or '',
            k['added_at']
        ])
    
    file = BytesIO(output.getvalue().encode('utf-8'))
    file.name = f"all_keys_{int(time.time())}.csv"
    
    await context.bot.send_document(
        chat_id=query.message.chat_id,
        document=file,
        caption=f"📥 *All Keys Export*\n\nTotal: {len(keys)} keys"
    )

async def admin_export_variant_keys(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Export keys for specific variant"""
    query = update.callback_query
    variant_id = int(query.data.split("_")[-1])
    
    keys = await db.fetch("""
        SELECT k.*, v.name as variant_name, p.name as product_name
        FROM keys k
        JOIN variants v ON v.id = k.variant_id
        JOIN products p ON p.id = v.product_id
        WHERE k.variant_id = $1
        ORDER BY k.status, k.id
    """, variant_id)
    
    if not keys:
        await query.answer("❌ No keys found!", show_alert=True)
        return
    
    key = keys[0]
    keys_text = "\n".join([f"{k['status'].upper()}: {k['key_data']}" for k in keys])
    file = BytesIO(keys_text.encode('utf-8'))
    file.name = f"{key['product_name']}_{key['variant_name']}_keys.txt"
    
    await context.bot.send_document(
        chat_id=query.message.chat_id,
        document=file,
        caption=f"📥 Keys for {key['product_name']} - {key['variant_name']}\n\nTotal: {len(keys)}"
    )

async def admin_delete_keys_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete keys - select product"""
    query = update.callback_query
    
    products = await db.fetch("""
        SELECT DISTINCT p.*
        FROM products p
        JOIN variants v ON v.product_id = p.id
        JOIN keys k ON k.variant_id = v.id
        WHERE k.status = 'available'
        ORDER BY p.id
    """)
    
    if not products:
        await query.answer("❌ No available keys to delete!", show_alert=True)
        return
    
    text = "*🗑 DELETE KEYS - Step 1/2*\n\n⚠️ This will delete AVAILABLE keys only.\n\nSelect product:"
    
    keyboard = []
    for p in products:
        keyboard.append([InlineKeyboardButton(
            f"{p['emoji']} {p['name']}", 
            callback_data=f"admin_delete_keys_prod_{p['id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_keys")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def admin_delete_keys_variant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete keys - select variant"""
    query = update.callback_query
    product_id = int(query.data.split("_")[-1])
    
    variants = await db.fetch("""
        SELECT v.*, 
               COUNT(k.id) FILTER (WHERE k.status = 'available') as available
        FROM variants v
        LEFT JOIN keys k ON k.variant_id = v.id
        WHERE v.product_id = $1
        GROUP BY v.id
        HAVING COUNT(k.id) FILTER (WHERE k.status = 'available') > 0
        ORDER BY v.validity_days
    """, product_id)
    
    if not variants:
        await query.answer("❌ No available keys in this product!", show_alert=True)
        return
    
    text = "*🗑 DELETE KEYS - Step 2/2*\n\nSelect variant:"
    
    keyboard = []
    for v in variants:
        inr_price = int(v['price'] * USD_TO_INR)
        keyboard.append([InlineKeyboardButton(
            f"{v['name']} - ₹{inr_price} ({v['available']} keys)", 
            callback_data=f"admin_confirm_delete_keys_{v['id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_delete_keys_menu")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def admin_confirm_delete_keys(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm key deletion"""
    query = update.callback_query
    variant_id = int(query.data.split("_")[-1])
    
    variant = await db.fetchrow("""
        SELECT v.*, p.name as product_name, p.emoji
        FROM variants v
        JOIN products p ON p.id = v.product_id
        WHERE v.id = $1
    """, variant_id)
    
    if not variant:
        await query.answer("❌ Variant not found!", show_alert=True)
        return
    
    available = await db.fetchval("SELECT COUNT(*) FROM keys WHERE variant_id = $1 AND status = 'available'", variant_id)
    
    inr_price = int(variant['price'] * USD_TO_INR)
    
    text = (
        f"*🗑 CONFIRM DELETION*\n\n"
        f"⚠️ *Are you sure?*\n\n"
        f"{variant['emoji']} {variant['product_name']}\n"
        f"Variant: {variant['name']} - ₹{inr_price}\n\n"
        f"*Will delete: {available} available keys*\n\n"
        f"Sold keys will NOT be deleted."
    )
    
    keyboard = [
        [InlineKeyboardButton("❌ Yes, Delete All Available Keys", callback_data=f"admin_do_delete_keys_{variant_id}")],
        [InlineKeyboardButton("✅ No, Keep Them", callback_data="admin_keys")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def admin_do_delete_keys(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Actually delete the keys"""
    query = update.callback_query
    variant_id = int(query.data.split("_")[-1])
    
    deleted = await db.execute("DELETE FROM keys WHERE variant_id = $1 AND status = 'available'", variant_id)
    
    await query.answer(f"✅ Deleted available keys!", show_alert=True)
    await admin_keys_menu(update, context)

# ═══════════════════════════════════════════════════════════
# 📊 REPORTS & USERS
# ═══════════════════════════════════════════════════════════

async def admin_reports_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reports menu"""
    query = update.callback_query
    
    # Get stats
    total_sales = await db.fetchval("SELECT COUNT(*) FROM orders WHERE status = 'success'")
    revenue = await db.fetchval("SELECT COALESCE(SUM(amount), 0) FROM orders WHERE status = 'success'")
    today_sales = await db.fetchval("SELECT COUNT(*) FROM orders WHERE status = 'success' AND DATE(completed_at) = CURRENT_DATE")
    today_revenue = await db.fetchval("SELECT COALESCE(SUM(amount), 0) FROM orders WHERE status = 'success' AND DATE(completed_at) = CURRENT_DATE")
    
    # Convert to INR for display
    revenue_inr = int(float(revenue) * USD_TO_INR)
    today_revenue_inr = int(float(today_revenue) * USD_TO_INR)
    
    text = (
        f"*📊 SALES REPORTS*\n\n"
        f"*All Time:*\n"
        f"├ Sales: {total_sales}\n"
        f"└ Revenue: ₹{revenue_inr}\n\n"
        f"*Today:*\n"
        f"├ Sales: {today_sales}\n"
        f"└ Revenue: ₹{today_revenue_inr}\n\n"
        f"*Actions:*"
    )
    
    keyboard = [
        [InlineKeyboardButton("📥 Export Sales CSV", callback_data="admin_export_csv")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_home")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def admin_export_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Export sales as CSV"""
    query = update.callback_query
    
    await query.answer("📥 Exporting sales...")
    
    sales = await db.fetch("""
        SELECT s.*, u.username, u.phone_number
        FROM sales s
        LEFT JOIN users u ON u.user_id = s.user_id
        ORDER BY s.sale_date DESC
    """)
    
    if not sales:
        await query.answer("❌ No sales to export!", show_alert=True)
        return
    
    # Create CSV
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['Order_ID', 'User_ID', 'Username', 'Phone', 'Product', 'Variant', 'Amount_USD', 'Key', 'Date'])
    
    for s in sales:
        usd_amount = s['amount'] / USD_TO_INR if s['amount'] else 0
        writer.writerow([
            s['order_id'],
            s['user_id'],
            s['username'] or '',
            s['phone_number'] or '',
            s['product_name'],
            s['variant_name'],
            f"{usd_amount:.2f}",
            s['key_delivered'],
            s['sale_date']
        ])
    
    file = BytesIO(output.getvalue().encode('utf-8'))
    file.name = f"sales_{int(time.time())}.csv"
    
    await context.bot.send_document(
        chat_id=query.message.chat_id,
        document=file,
        caption=f"📥 *Sales Export*\n\nTotal: {len(sales)} sales",
        parse_mode="Markdown"
    )

async def admin_users_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Users management menu"""
    query = update.callback_query
    
    total = await db.fetchval("SELECT COUNT(*) FROM users")
    blocked = await db.fetchval("SELECT COUNT(*) FROM users WHERE is_blocked = TRUE")
    today = await db.fetchval("SELECT COUNT(*) FROM users WHERE DATE(joined_at) = CURRENT_DATE")
    
    text = (
        f"*👥 USER MANAGEMENT*\n\n"
        f"📊 *Statistics:*\n"
        f"├ 👤 Total Users: *{total}*\n"
        f"├ 🚫 Blocked: *{blocked}*\n"
        f"├ ⭐ Joined Today: *{today}*\n"
        f"└ 🎯 Choose an action below:\n"
    )
    
    keyboard = [
        [InlineKeyboardButton("👀 View Users", callback_data="admin_view_users")],
        [InlineKeyboardButton("🚫 Block User", callback_data="admin_block_user_prompt")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_home")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def admin_view_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View recent users"""
    query = update.callback_query
    
    users = await db.fetch("""
        SELECT u.*, 
               COUNT(DISTINCT o.order_id) FILTER (WHERE o.status = 'success') as purchases
        FROM users u
        LEFT JOIN orders o ON o.user_id = u.user_id
        GROUP BY u.user_id
        ORDER BY u.joined_at DESC
        LIMIT 20
    """)
    
    text = "*👥 RECENT USERS (Last 20):*\n\n"
    
    for u in users:
        status = "🚫" if u['is_blocked'] else "✅"
        text += (
            f"{status} `{u['user_id']}` - {u['first_name']}\n"
            f"├ @{u['username'] or 'none'}\n"
            f"└ Purchases: {u['purchases']}\n\n"
        )
    
    keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="admin_users")]]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

async def admin_block_user_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt for user ID to block"""
    query = update.callback_query
    user_id = query.from_user.id
    
    admin_states[user_id] = {'action': 'block_user'}
    
    text = (
        "*🚫 BLOCK USER*\n\n"
        "📝 Send the user ID to block:\n\n"
        "├ 💡 Example: `123456789`\n"
        "└ 🔓 Or send `unblock 123456789` to unblock"
    )
    
    await query.edit_message_text(text, parse_mode="Markdown")
    await query.answer()

# ═══════════════════════════════════════════════════════════
# 📢 BROADCAST
# ═══════════════════════════════════════════════════════════

async def admin_broadcast_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast menu"""
    query = update.callback_query
    user_id = query.from_user.id
    
    admin_states[user_id] = {'action': 'broadcast'}
    
    total_users = await db.fetchval("SELECT COUNT(*) FROM users WHERE is_blocked = FALSE")
    
    text = (
        f"*📢 BROADCAST MESSAGE*\n\n"
        f"├ 👥 Target: *{total_users}* active users\n"
        f"├ 📝 Supports: Text, Photos, Videos\n"
        f"└ 🎯 Send your message below:\n"
    )
    
    keyboard = [[InlineKeyboardButton("⬅️ Cancel", callback_data="admin_home")]]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    await query.answer()

# ═══════════════════════════════════════════════════════════
# 💬 ADMIN MESSAGE HANDLER (For text inputs)
# ═══════════════════════════════════════════════════════════

async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all admin text and file inputs"""
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        return
    
    # ═══════════════════════════════════════════════════════════
    # HANDLE BUTTON-BASED INPUTS (Custom Pricing & Resellers)
    # ═══════════════════════════════════════════════════════════
    waiting_for = context.user_data.get('waiting_for')
    
    if waiting_for == 'custom_price_amount':
        # Handle custom amount input IN USD
        try:
            usd_amount = float(update.message.text.strip())
            if usd_amount <= 0:
                await update.message.reply_text("❌ Amount must be positive!")
                return
            
            context.user_data['custom_price_usd'] = usd_amount
            context.user_data['waiting_for'] = 'custom_price_chatid'
            
            variant_id = context.user_data.get('custom_price_variant_id')
            variant = await db.fetchrow("""
                SELECT v.*, p.name as product_name, p.emoji
                FROM variants v
                JOIN products p ON p.id = v.product_id
                WHERE v.id = $1
            """, variant_id)
            
            inr_amount = int(usd_amount * USD_TO_INR)
            
            await update.message.reply_text(
                f"*💎 Set Custom Price*\n━━━━━━━━━━━━━━━━━━━\n\n"
                f"*Step 5: Enter User Chat ID*\n\n"
                f"{variant['emoji']} {variant['product_name']} ├ {variant['name']}\n"
                f"Custom Price: *${usd_amount:.2f}* (₹{inr_amount})\n\n"
                f"*Reply with user's Chat ID:*\n"
                f"Example: `123456789`",
                parse_mode="Markdown"
            )
            return
        except:
            await update.message.reply_text("❌ Invalid amount! Reply with numbers only (e.g., 2.50 for $2.50)")
            return
    
    elif waiting_for == 'custom_price_chatid':
        # Handle chat ID input for custom pricing
        try:
            chat_id = int(update.message.text.strip())
            variant_id = context.user_data.get('custom_price_variant_id')
            usd_price = context.user_data.get('custom_price_usd')
            
            # Save custom price
            await db.execute("""
                INSERT INTO custom_pricing (user_id, variant_id, custom_price)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id, variant_id) 
                DO UPDATE SET custom_price = $3, is_active = TRUE
            """, chat_id, variant_id, usd_price)
            
            inr_price = int(usd_price * USD_TO_INR)
            
            await update.message.reply_text(
                f"✅ *Custom Price Set!*\n\n"
                f"User: `{chat_id}`\n"
                f"Price: *₹{inr_price}* (${usd_price:.2f})\n\n"
                f"User will see this price automatically!",
                parse_mode="Markdown"
            )
            
            # Clear state
            context.user_data.clear()
            return
        except:
            await update.message.reply_text("❌ Invalid Chat ID! Reply with numbers only (e.g., 123456789)")
            return
    
    elif waiting_for == 'reseller_discount':
        # Handle custom discount % input
        try:
            discount = int(update.message.text.strip())
            if discount <= 0 or discount >= 100:
                await update.message.reply_text("❌ Discount must be between 1-99!")
                return
            
            context.user_data['reseller_discount'] = discount
            context.user_data['waiting_for'] = 'reseller_chatid'
            
            await update.message.reply_text(
                f"*🎁 Add Reseller*\n━━━━━━━━━━━━━━━━━━━\n\n"
                f"*Step 3: Enter User Chat ID*\n\n"
                f"Discount: *{discount}%*\n\n"
                f"*Reply with user's Chat ID:*\n"
                f"Example: `123456789`",
                parse_mode="Markdown"
            )
            return
        except:
            await update.message.reply_text("❌ Invalid discount! Reply with numbers only (e.g., 15)")
            return
    
    elif waiting_for == 'reseller_chatid':
        # Handle chat ID input for reseller
        try:
            chat_id = int(update.message.text.strip())
            discount = context.user_data.get('reseller_discount')
            
            # Save reseller
            await db.execute("""
                INSERT INTO resellers (user_id, discount_percent)
                VALUES ($1, $2)
                ON CONFLICT (user_id) 
                DO UPDATE SET discount_percent = $2, is_active = TRUE
            """, chat_id, discount)
            
            await update.message.reply_text(
                f"✅ *Reseller Added!*\n\n"
                f"User: `{chat_id}`\n"
                f"Discount: *{discount}%*\n\n"
                f"User gets {discount}% off on ALL products!",
                parse_mode="Markdown"
            )
            
            # Clear state
            context.user_data.clear()
            return
        except:
            await update.message.reply_text("❌ Invalid Chat ID! Reply with numbers only (e.g., 123456789)")
            return
    
    # ═══════════════════════════════════════════════════════════
    # ORIGINAL ADMIN STATES HANDLING
    # ═══════════════════════════════════════════════════════════
    if user_id not in admin_states:
        return
    
    state = admin_states[user_id]
    action = state.get('action')
    
    # ═══════════════════════════════════════════════════════════
    # ADD PRODUCT
    # ═══════════════════════════════════════════════════════════
    if action == 'add_product':
        step = state.get('step')
        
        if step == 1:  # Product name
            name = update.message.text.strip()
            
            # Check duplicate
            exists = await db.fetchval("SELECT id FROM products WHERE name = $1", name)
            if exists:
                await update.message.reply_text("❌ Product with this name already exists!")
                return
            
            state['name'] = name
            state['step'] = 2
            
            await update.message.reply_text(
                "*Step 2/4*\n\nSend the product *description*.\n\nExample: `Premium Fortnite accounts with skins`",
                parse_mode="Markdown"
            )
        
        elif step == 2:  # Description
            desc = update.message.text.strip()
            state['description'] = desc
            state['step'] = 3
            
            await update.message.reply_text(
                "*Step 3/4 - Group Link (Optional)*\n\n"
                "Send the *Telegram group/channel link* for this product.\n\n"
                "Customers will get this link with their key after purchase.\n\n"
                "Examples:\n"
                "• https://t.me/yourgroup\n"
                "• https://t.me/+invite123\n\n"
                "Or send `-` to skip (no group).",
                parse_mode="Markdown"
            )
        
        elif step == 3:  # Group Link (optional)
            group_link = update.message.text.strip()
            
            # Allow "skip" or "-" for no group
            if group_link.lower() in ['skip', '-', 'no', 'none']:
                group_link = None
            
            state['group_link'] = group_link
            state['emoji'] = '🎮'  # DEFAULT EMOJI - NO NEED TO ASK!
            state['step'] = 4
            
            group_text = f"🔗 Group: {group_link}" if group_link else "🔗 Group: None"
            
            await update.message.reply_text(
                f"*Step 4/4 - Confirm*\n\n"
                f"🎮 *{state['name']}*\n"
                f"{state['description']}\n"
                f"{group_text}\n\n"
                f"Send `yes` to create this product.",
                parse_mode="Markdown"
            )
        
        elif step == 4:  # Confirm
            if update.message.text.strip().lower() == 'yes':
                await db.execute(
                    "INSERT INTO products (name, description, emoji, group_link) VALUES ($1, $2, $3, $4)",
                    state['name'], state['description'], state['emoji'], state.get('group_link')
                )
                
                del admin_states[user_id]
                
                group_info = f"\n🔗 Group: {state.get('group_link')}" if state.get('group_link') else ""
                
                await update.message.reply_text(
                    f"✅ *Product created!*\n\n{state['emoji']} {state['name']}{group_info}\n\n"
                    f"Now add variants (price plans) using `/admin` → Manage Products → Edit Product.",
                    parse_mode="Markdown"
                )
            else:
                del admin_states[user_id]
                await update.message.reply_text("❌ Cancelled.")
    
    # ═══════════════════════════════════════════════════════════
    # ADD VARIANT
    # ═══════════════════════════════════════════════════════════
    elif action == 'add_variant':
        step = state.get('step')
        
        if step == 1:  # Variant name
            name = update.message.text.strip()
            state['variant_name'] = name
            state['step'] = 2
            
            await update.message.reply_text(
                "*Step 2/3*\n\nSend the *price in USD ($)*.\n\n"
                "Examples:\n"
                "├ `2` for $2.00 (₹180)\n"
                "├ `5` for $5.00 (₹450)\n"
                "└ `10` for $10.00 (₹900)\n\n"
                "💡 *Tip:* Use whole dollars for perfect INR conversion!",
                parse_mode="Markdown"
            )
        
        elif step == 2:  # Price
            try:
                # Admin enters USD - NO rounding bugs!
                usd_price = float(update.message.text.strip())
                if usd_price <= 0:
                    raise ValueError
                
                state['price'] = usd_price
                state['step'] = 3
                
                # Show INR equivalent
                inr_price = int(usd_price * USD_TO_INR)
                
                await update.message.reply_text(
                    "*Step 3/3*\n\nSend the *validity in days*.\n\n"
                    f"Price set: ${usd_price:.2f} (₹{inr_price})\n\n"
                    "Example: `30` for 1 month\n"
                    "Or: `999` for lifetime",
                    parse_mode="Markdown"
                )
            except:
                await update.message.reply_text("❌ Invalid price! Send a USD amount like `2` or `5.50`")
        
        elif step == 3:  # Validity
            try:
                days = int(update.message.text.strip())
                if days <= 0:
                    raise ValueError
                
                # Check duplicate
                exists = await db.fetchval(
                    "SELECT id FROM variants WHERE product_id = $1 AND validity_days = $2",
                    state['product_id'], days
                )
                
                if exists:
                    await update.message.reply_text("❌ A variant with this validity already exists!")
                    return
                
                # Create variant
                await db.execute("""
                    INSERT INTO variants (product_id, name, price, validity_days)
                    VALUES ($1, $2, $3, $4)
                """, state['product_id'], state['variant_name'], state['price'], days)
                
                inr_price = int(state['price'] * USD_TO_INR)
                
                del admin_states[user_id]
                
                await update.message.reply_text(
                    f"✅ *Variant created!*\n\n"
                    f"Name: {state['variant_name']}\n"
                    f"Price: *${state['price']:.2f}* (₹{inr_price})\n"
                    f"Validity: {days} days\n\n"
                    f"✅ Users will see: ₹{inr_price}\n"
                    f"✅ Payment amount: ₹{inr_price}\n\n"
                    f"Now upload keys using `/admin` → Manage Keys → Upload Keys.",
                    parse_mode="Markdown"
                )
            except:
                await update.message.reply_text("❌ Invalid days! Send a number like `30` or `999`")
    
    # ═══════════════════════════════════════════════════════════
    # EDIT VARIANT PRICE
    # ═══════════════════════════════════════════════════════════
    elif action == 'edit_variant_price':
        try:
            # Admin enters USD - NO rounding bugs!
            usd_price = float(update.message.text.strip())
            if usd_price <= 0:
                raise ValueError
            
            # Calculate INR for display (will be exact if USD is whole number!)
            inr_price = int(usd_price * USD_TO_INR)
            variant_id = state['variant_id']
            
            # Store exact USD in database
            await db.execute("UPDATE variants SET price = $1 WHERE id = $2", usd_price, variant_id)
            
            variant = await db.fetchrow("SELECT * FROM variants WHERE id = $1", variant_id)
            
            del admin_states[user_id]
            
            await update.message.reply_text(
                f"✅ *Price updated!*\n\n"
                f"Variant: {variant['name']}\n"
                f"New Price: *${usd_price:.2f}* (₹{inr_price})\n\n"
                f"✅ Display: ₹{inr_price}\n"
                f"✅ Payment: ₹{inr_price}\n"
                f"✅ Database: ₹{inr_price}\n\n"
                f"*All prices match perfectly!* 🎉",
                parse_mode="Markdown"
            )
        except:
            await update.message.reply_text("❌ Invalid price! Send a USD amount like `2` or `5.50`")
    
    # ═══════════════════════════════════════════════════════════
    # UPLOAD KEYS
    # ═══════════════════════════════════════════════════════════
    elif action == 'upload_keys':
        text = update.message.text.strip()
        keys = [k.strip() for k in text.split('\n') if k.strip()]
        
        if not keys:
            await update.message.reply_text("❌ No keys found! Send one key per line.")
            return
        
        variant_id = state['variant_id']
        
        # Add keys with proper duplicate detection
        added = 0
        duplicates = 0
        errors = 0
        for key in keys:
            try:
                await db.execute("INSERT INTO keys (variant_id, key_data) VALUES ($1, $2)", variant_id, key)
                added += 1
            except asyncpg.UniqueViolationError:
                # This key already exists in database
                duplicates += 1
                logger.info(f"Duplicate key skipped: {key[:20]}...")
            except Exception as e:
                # Other error
                errors += 1
                logger.error(f"Key upload error: {e}")
        
        del admin_states[user_id]
        
        response = f"✅ *Keys uploaded!*\n\n├ Added: *{added}*\n└ Duplicates: *{duplicates}*"
        if errors > 0:
            response += f"\n└ Errors: *{errors}*"
        
        await update.message.reply_text(response, parse_mode="Markdown")
    
    # ═══════════════════════════════════════════════════════════
    # BLOCK USER
    # ═══════════════════════════════════════════════════════════
    elif action == 'block_user':
        text = update.message.text.strip().lower()
        
        if text.startswith('unblock '):
            target_id = int(text.replace('unblock ', '').strip())
            await db.execute("UPDATE users SET is_blocked = FALSE WHERE user_id = $1", target_id)
            await update.message.reply_text(f"✅ User `{target_id}` unblocked!", parse_mode="Markdown")
        else:
            target_id = int(text)
            await db.execute("UPDATE users SET is_blocked = TRUE WHERE user_id = $1", target_id)
            await update.message.reply_text(f"🚫 User `{target_id}` blocked!", parse_mode="Markdown")
        
        del admin_states[user_id]
    
    # ═══════════════════════════════════════════════════════════
    # SEARCH ORDER
    # ═══════════════════════════════════════════════════════════
    elif action == 'search_order':
        order_id = update.message.text.strip()
        del admin_states[user_id]
        await show_order_details(update, context, order_id)
        return
    
    # ═══════════════════════════════════════════════════════════
    # EDIT PRODUCT NAME - NEW FEATURE
    # ═══════════════════════════════════════════════════════════
    elif action == 'edit_product_name':
        new_name = update.message.text.strip()
        product_id = state['product_id']
        
        try:
            await db.execute("UPDATE products SET name = $1 WHERE id = $2", new_name, product_id)
            await update.message.reply_text(
                f"✅ *Product name updated!*\n\n"
                f"Old: {state['old_name']}\n"
                f"New: {new_name}",
                parse_mode="Markdown"
            )
        except:
            await update.message.reply_text("❌ Error updating name! Name might already exist.")
        
        del admin_states[user_id]
        return
    
    # ═══════════════════════════════════════════════════════════
    # EDIT GROUP LINK
    # ═══════════════════════════════════════════════════════════
    elif action == 'edit_group_link':
        new_link = update.message.text.strip()
        product_id = state['product_id']
        
        # Remove link if dash
        if new_link.lower() in ['-', 'remove', 'delete', 'none']:
            new_link = None
        
        await db.execute("UPDATE products SET group_link = $1 WHERE id = $2", new_link, product_id)
        
        del admin_states[user_id]
        
        link_text = f"Updated to: {new_link}" if new_link else "Removed"
        await update.message.reply_text(
            f"✅ *Group link updated!*\n\n"
            f"Product: *{state['product_name']}*\n"
            f"{link_text}",
            parse_mode="Markdown"
        )
        return
    
    # ═══════════════════════════════════════════════════════════
    # BROADCAST
    # ═══════════════════════════════════════════════════════════
    elif action == 'broadcast':
        users = await db.fetch("SELECT user_id FROM users WHERE is_blocked = FALSE")
        
        sent = 0
        failed = 0
        
        await update.message.reply_text(f"📢 Broadcasting to {len(users)} users...")
        
        for u in users:
            try:
                if update.message.photo:
                    await context.bot.send_photo(
                        chat_id=u['user_id'],
                        photo=update.message.photo[-1].file_id,
                        caption=update.message.caption
                    )
                elif update.message.video:
                    await context.bot.send_video(
                        chat_id=u['user_id'],
                        video=update.message.video.file_id,
                        caption=update.message.caption
                    )
                elif update.message.text:
                    await context.bot.send_message(
                        chat_id=u['user_id'],
                        text=update.message.text
                    )
                
                sent += 1
            except:
                failed += 1
            
            await asyncio.sleep(0.05)  # Rate limit
        
        del admin_states[user_id]
        
        await update.message.reply_text(
            f"✅ *Broadcast complete!*\n\n"
            f"├ Sent: {sent}\n"
            f"└ Failed: {failed}",
            parse_mode="Markdown"
        )

async def bulk_add_keys(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bulk add keys from file - /bulk <variant_id>"""
    if update.effective_user.id not in ADMIN_IDS:
        return
    
    if not update.message.reply_to_message or not update.message.reply_to_message.document:
        await update.message.reply_text("❌ Reply to a .txt file with `/bulk <variant_id>`", parse_mode="Markdown")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Usage: `/bulk <variant_id>`\n\nExample: `/bulk 1`", parse_mode="Markdown")
        return
    
    try:
        vid = int(context.args[0])
        
        # Check variant exists
        variant = await db.fetchrow("SELECT * FROM variants WHERE id = $1", vid)
        if not variant:
            await update.message.reply_text(f"❌ Variant ID {vid} not found!")
            return
        
        # Download file
        file = await update.message.reply_to_message.document.get_file()
        content = await file.download_as_bytearray()
        keys = content.decode('utf-8').strip().split('\n')
        keys = [k.strip() for k in keys if k.strip()]
        
        # Add keys with proper duplicate detection
        added = 0
        duplicates = 0
        errors = 0
        for key in keys:
            try:
                await db.execute("INSERT INTO keys (variant_id, key_data) VALUES ($1, $2)", vid, key)
                added += 1
            except asyncpg.UniqueViolationError:
                # This key already exists in database
                duplicates += 1
                logger.info(f"Duplicate key skipped: {key[:20]}...")
            except Exception as e:
                # Other error
                errors += 1
                logger.error(f"Key upload error: {e}")
        
        response = f"✅ *Keys uploaded!*\n\n├ Added: *{added}*\n└ Duplicates: *{duplicates}*\n\nVariant ID: `{vid}`"
        if errors > 0:
            response += f"\n└ Errors: *{errors}*"
        
        await update.message.reply_text(response, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

# ═══════════════════════════════════════════════════════════
# 🌐 WEBHOOK SERVER - CRITICAL FIX FOR FORM DATA!
# ═══════════════════════════════════════════════════════════

async def payment_webhook_handler(request):
    """
    🚀 FIXED WEBHOOK HANDLER!
    
    The payment gateway sends FORM-ENCODED DATA, not JSON!
    PHP bot uses: $_POST['order_id']
    We need to use: await request.post()
    """
    try:
        # 🔥 THE REAL FIX: Read form data first!
        try:
            data = dict(await request.post())  # Form-encoded data
            logger.info(f"📥 Webhook received FORM data: {data}")
        except:
            try:
                data = await request.json()  # Fallback to JSON
                logger.info(f"📥 Webhook received JSON data: {data}")
            except:
                data = {}
                logger.error("❌ Webhook: Could not read data!")
        
        # Handle different field names
        order_id = data.get('order_id') or data.get('orderId')
        status = data.get('status') or data.get('txnStatus')
        utr = data.get('utr', '')
        
        if not order_id:
            logger.error(f"❌ Webhook: Missing order_id in {data}")
            return web.Response(text="Missing order_id", status=400)
        
        logger.info(f"📥 Webhook: {order_id} ├ Status: {status}")
        
        # Process if successful
        if str(status).upper() == 'SUCCESS':
            bot = request.app['bot']
            
            # Create a context-like wrapper for the bot (webhook doesn't have full context)
            class BotContext:
                def __init__(self, bot):
                    self.bot = bot
            
            bot_context = BotContext(bot)
            await process_successful_payment(bot_context, order_id, utr)
            logger.info(f"✅ Webhook processed: {order_id}")
        
        return web.Response(text="OK", status=200)
        
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        return web.Response(text="Error", status=500)

async def payerurl_webhook_handler(request):
    """Handle PayerURL payment notifications"""
    try:
        logger.info("🔔 PayerURL webhook received")
        
        # Get authorization
        auth_header = request.headers.get('Authorization', '')
        
        if not auth_header:
            # Try POST data
            data = await request.post()
            auth_str_post = data.get('authStr', '')
            if auth_str_post:
                auth_header = 'Bearer ' + auth_str_post
        
        if not auth_header:
            logger.warning("⚠️ No authorization header")
            return web.json_response({'status': 2030, 'message': 'No authorization'})
        
        # Decode authorization
        auth_str = auth_header.replace('Bearer ', '')
        auth_decoded = base64.b64decode(auth_str).decode('utf-8')
        auth_parts = auth_decoded.split(':')
        
        public_key = auth_parts[0]
        signature = auth_parts[1] if len(auth_parts) > 1 else ''
        
        logger.info(f"🔑 Public key: {public_key}")
        logger.info(f"🔏 Signature received: {signature}")
        
        # Verify public key
        if public_key != PAYERURL_PUBLIC_KEY:
            logger.warning(f"⚠️ Public key mismatch: {public_key}")
            return web.json_response({'status': 2030, 'message': 'Public key mismatch'})
        
        # Get payment data - EXACTLY like PHP code (include ALL fields)
        data = await request.post()
        payment_data = {
            'order_id': data.get('order_id', ''),
            'ext_transaction_id': data.get('ext_transaction_id', ''),
            'transaction_id': data.get('transaction_id', ''),
            'status_code': data.get('status_code', ''),
            'note': data.get('note', ''),
            'confirm_rcv_amnt': data.get('confirm_rcv_amnt', ''),
            'confirm_rcv_amnt_curr': data.get('confirm_rcv_amnt_curr', ''),
            'coin_rcv_amnt': data.get('coin_rcv_amnt', ''),
            'coin_rcv_amnt_curr': data.get('coin_rcv_amnt_curr', ''),
            'txn_time': data.get('txn_time', '')
        }
        
        logger.info(f"💎 Payment data: {payment_data}")
        
        # Validate
        if not payment_data['transaction_id'] or not payment_data['order_id']:
            logger.warning("⚠️ Invalid payment data")
            return web.json_response({'status': 2050, 'message': 'Invalid data'})
        
        if payment_data['status_code'] == '20000':
            logger.info(f"❌ Payment cancelled: {payment_data['order_id']}")
            return web.json_response({'status': 20000, 'message': 'Order Cancelled'})
        
        if payment_data['status_code'] != '200':
            logger.warning(f"⚠️ Payment not complete: {payment_data['status_code']}")
            return web.json_response({'status': 2050, 'message': 'Order not complete'})
        
        # CORRECT SIGNATURE - EXACTLY LIKE PHP!
        # Step 1: Sort by keys
        sorted_data = dict(sorted(payment_data.items()))
        
        # Step 2: Build query string (like PHP's http_build_query - includes empty values!)
        # PHP: http_build_query(['a'=>'hello', 'b'=>'', 'c'=>'world']) = "a=hello&b=&c=world"
        # Python equivalent using urllib.parse.urlencode
        query_string = urlencode(sorted_data)
        
        logger.info(f"🔐 Query string for signature: {query_string}")
        
        # Step 3: HMAC-SHA256
        expected_signature = hmac.new(
            PAYERURL_SECRET_KEY.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        logger.info(f"🔐 Expected signature: {expected_signature}")
        logger.info(f"🔐 Received signature: {signature}")
        
        if expected_signature != signature:
            logger.error(f"❌ SIGNATURE MISMATCH!")
            logger.error(f"   Expected: {expected_signature}")
            logger.error(f"   Received: {signature}")
            logger.error(f"   Query string: {query_string}")
            logger.error(f"   Payment data: {payment_data}")
            return web.json_response({'status': 2030, 'message': 'Signature mismatch'})
        
        # Payment successful!
        order_id = payment_data['order_id']
        transaction_id = payment_data['transaction_id']
        
        logger.info(f"✅ PayerURL payment SUCCESS: {order_id}")
        
        # Update order with transaction ID
        await db.execute(
            "UPDATE orders SET payerurl_txn_id = $1 WHERE order_id = $2",
            transaction_id,
            order_id
        )
        
        # Process payment
        bot = request.app['bot']
        
        class BotContext:
            def __init__(self, bot):
                self.bot = bot
        
        bot_context = BotContext(bot)
        await process_successful_payment(bot_context, order_id, transaction_id)
        
        return web.json_response({'status': 200, 'message': 'Payment processed'})
        
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return web.json_response({'status': 500, 'message': 'Internal error'})


async def bkash_webhook_handler(request):
    """Handle bKash SMS verification - FRAUD PROTECTED"""
    try:
        body = await request.read()
        body_str = body.decode('utf-8')
        
        try:
            sms_data = json.loads(body_str)
        except json.JSONDecodeError:
            return web.json_response({'status': 'error', 'message': 'Invalid JSON'}, status=400)
        
        logger.info(f"📱 bKash SMS received: {sms_data}")
        
        # SECURITY CHECK #1: Verify API key
        api_key = sms_data.get('api_key', '')
        if api_key != BKASH_API_KEY:
            logger.error(f"❌ Invalid API key from webhook!")
            return web.json_response({'status': 'error', 'message': 'Unauthorized'}, status=401)
        
        required = ['sender', 'amount', 'txn_id', 'balance', 'timestamp', 'from_number']
        if not all(k in sms_data for k in required):
            return web.json_response({'status': 'error', 'message': 'Missing fields'}, status=400)
        
        # FRAUD CHECK #1: Verify sender is bKash (check for "16216" OR "bKash" contact name)
        sender = sms_data['sender']
        is_bkash = sender == BKASH_SENDER or sender.lower() == 'bkash'
        
        if not is_bkash:
            logger.warning(f"⚠️ SMS from unauthorized sender: {sender}")
            return web.json_response({'status': 'error', 'message': 'Unauthorized sender'}, status=403)
        
        txn_id = sms_data['txn_id']
        amount = float(sms_data['amount'])
        from_number = sms_data['from_number']
        balance = float(sms_data['balance'])
        
        # FRAUD CHECK #2: Check duplicate TRX ID
        existing = await db.fetchrow(
            "SELECT * FROM bkash_transactions WHERE txn_id = $1",
            txn_id
        )
        
        if existing:
            logger.info(f"Transaction {txn_id} already processed")
            return web.json_response({'status': 'ok', 'message': 'Already processed'})
        
        # Save transaction
        await db.execute(
            """
            INSERT INTO bkash_transactions (txn_id, sender, amount, fee, balance, received_at, processed)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            txn_id, from_number, amount, 0, balance, 
            datetime.fromisoformat(sms_data['timestamp']), False
        )
        
        # FRAUD CHECK #3: Match with pending order (Amount + TRX ID)
        order = await db.fetchrow(
            """
            SELECT * FROM orders 
            WHERE payment_method = 'bkash' 
            AND status = 'pending' 
            AND price = $1
            AND bkash_txn_id = $2
            ORDER BY created_at DESC
            LIMIT 1
            """,
            amount, txn_id
        )
        
        if order:
            # ✅ ALL 3 CHECKS PASSED: Official bKash + Amount match + TRX ID match
            await db.execute(
                """
                UPDATE orders 
                SET bkash_sender = $1, bkash_amount = $2, status = 'completed', completed_at = $3
                WHERE order_id = $4
                """,
                from_number, amount, datetime.now(), order['order_id']
            )
            
            await db.execute(
                "UPDATE bkash_transactions SET processed = TRUE, order_id = $1 WHERE txn_id = $2",
                order['order_id'], txn_id
            )
            
            # Deliver key
            bot = request.app['bot']
            
            class BotContext:
                def __init__(self, bot):
                    self.bot = bot
            
            bot_context = BotContext(bot)
            await process_successful_payment(bot_context, order['order_id'], txn_id)
            
            logger.info(f"✅ bKash payment verified: {order['order_id']}")
        else:
            logger.warning(f"⚠️ No matching order: {amount} BDT, TXN: {txn_id}")
            
            for admin_id in ADMIN_IDS:
                try:
                    await request.app['bot'].send_message(
                        admin_id,
                        f"⚠️ **UNMATCHED BKASH PAYMENT**\n\n"
                        f"Amount: {amount} BDT\n"
                        f"TRX ID: `{txn_id}`\n"
                        f"From: {from_number}\n"
                        f"Balance: {balance} BDT\n\n"
                        f"No matching order!",
                        parse_mode='Markdown'
                    )
                except:
                    pass
        
        return web.json_response({'status': 'ok', 'message': 'Payment processed'})
        
    except Exception as e:
        logger.error(f"❌ bKash webhook error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return web.json_response({'status': 'error', 'message': 'Internal error'}, status=500)


async def health_check(request):
    """Health check endpoint"""
    return web.Response(text="Fuck You Bitch 😂")

# ═══════════════════════════════════════════════════════════
# 🚀 MAIN APPLICATION
# ═══════════════════════════════════════════════════════════

async def main():
    """Main application"""
    global application
    
    # Connect database
    await db.connect()
    logger.info("✅ Database connected - all tables auto-created!")
    
    # Build bot
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # ═══════════════════════════════════════════════════════════
    # CLIENT HANDLERS (Button-based)
    # ═══════════════════════════════════════════════════════════
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    application.add_handler(CallbackQueryHandler(show_main_menu, pattern="^start_menu$"))
    application.add_handler(CallbackQueryHandler(show_variants, pattern="^prod_"))
    application.add_handler(CallbackQueryHandler(my_orders, pattern="^my_orders$"))
    application.add_handler(CallbackQueryHandler(initiate_payment, pattern="^buy_"))
    
    # Payment method selection
    application.add_handler(CallbackQueryHandler(initiate_upi_payment, pattern="^pay_upi_"))
    application.add_handler(CallbackQueryHandler(initiate_crypto_payment, pattern="^pay_crypto_"))
    application.add_handler(CallbackQueryHandler(initiate_bkash_payment, pattern="^pay_bkash_"))
    application.add_handler(CallbackQueryHandler(bkash_verify_prompt, pattern="^bkash_verify_"))
    
    # ═══════════════════════════════════════════════════════════
    # ADMIN HANDLERS (Button-based + Commands)
    # ═══════════════════════════════════════════════════════════
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("bulk", bulk_add_keys))
    application.add_handler(CommandHandler("test", admin_test_pricing))  # Debug command
    
    # Admin navigation
    application.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_home$"))
    application.add_handler(CallbackQueryHandler(admin_exit, pattern="^admin_exit$"))
    
    # NEW FEATURES - Order History, Search Order, Analytics, Custom Pricing
    application.add_handler(CallbackQueryHandler(admin_order_history, pattern="^admin_order_history$"))
    application.add_handler(CallbackQueryHandler(admin_search_order, pattern="^admin_search_order$"))
    application.add_handler(CallbackQueryHandler(admin_analytics, pattern="^admin_analytics$"))
    application.add_handler(CallbackQueryHandler(admin_custom_pricing, pattern="^admin_custom_pricing$"))
    
    # Custom Pricing Button Flow
    application.add_handler(CallbackQueryHandler(admin_set_custom_price_step1, pattern="^admin_set_custom_price_step1$"))
    application.add_handler(CallbackQueryHandler(admin_set_custom_price_step2, pattern="^admin_custprice_prod_"))
    application.add_handler(CallbackQueryHandler(admin_set_custom_price_step3, pattern="^admin_custprice_var_"))
    application.add_handler(CallbackQueryHandler(admin_set_custom_price_step4, pattern="^admin_custprice_amt_"))
    application.add_handler(CallbackQueryHandler(admin_remove_custom_price, pattern="^admin_remove_custom_price$"))
    application.add_handler(CallbackQueryHandler(admin_do_remove_custom_price, pattern="^admin_do_remove_custprice_"))
    
    # Products
    application.add_handler(CallbackQueryHandler(admin_products_menu, pattern="^admin_products$"))
    application.add_handler(CallbackQueryHandler(admin_add_variant_menu, pattern="^admin_add_variant_menu$"))
    application.add_handler(CallbackQueryHandler(admin_add_product_step1, pattern="^admin_add_product_step1$"))
    application.add_handler(CallbackQueryHandler(admin_edit_products_list, pattern="^admin_edit_products$"))
    application.add_handler(CallbackQueryHandler(admin_edit_product, pattern="^admin_edit_prod_"))
    application.add_handler(CallbackQueryHandler(admin_edit_product_name, pattern="^admin_edit_name_"))
    application.add_handler(CallbackQueryHandler(admin_toggle_product, pattern="^admin_toggle_product_"))
    application.add_handler(CallbackQueryHandler(admin_edit_group_link, pattern="^admin_edit_group_link_"))
    application.add_handler(CallbackQueryHandler(admin_delete_product_list, pattern="^admin_delete_product$"))
    application.add_handler(CallbackQueryHandler(admin_confirm_delete_product, pattern="^admin_confirm_delete_prod_"))
    application.add_handler(CallbackQueryHandler(admin_do_delete_product, pattern="^admin_do_delete_prod_"))
    
    # Variants
    application.add_handler(CallbackQueryHandler(admin_add_variant_step1, pattern="^admin_add_variant_step1_"))
    application.add_handler(CallbackQueryHandler(admin_edit_variant_price_menu, pattern="^admin_edit_variant_price_"))
    application.add_handler(CallbackQueryHandler(admin_edit_variant_price_prompt, pattern="^admin_edit_price_var_"))
    application.add_handler(CallbackQueryHandler(admin_delete_variant_list, pattern="^admin_delete_variant_"))
    application.add_handler(CallbackQueryHandler(admin_confirm_delete_variant, pattern="^admin_confirm_delete_var_"))
    application.add_handler(CallbackQueryHandler(admin_do_delete_variant, pattern="^admin_do_delete_var_"))
    
    # Keys
    application.add_handler(CallbackQueryHandler(admin_keys_menu, pattern="^admin_keys$"))
    application.add_handler(CallbackQueryHandler(admin_upload_keys_easy, pattern="^admin_upload_keys_easy$"))
    application.add_handler(CallbackQueryHandler(admin_easy_upload_variant, pattern="^admin_easy_upload_prod_"))
    application.add_handler(CallbackQueryHandler(admin_easy_upload_prompt, pattern="^admin_easy_upload_var_"))
    application.add_handler(CallbackQueryHandler(admin_view_keys_menu, pattern="^admin_view_keys$"))
    application.add_handler(CallbackQueryHandler(admin_view_keys_variant, pattern="^admin_view_keys_prod_"))
    application.add_handler(CallbackQueryHandler(admin_show_keys, pattern="^admin_show_keys_"))
    application.add_handler(CallbackQueryHandler(admin_export_keys, pattern="^admin_export_keys$"))
    application.add_handler(CallbackQueryHandler(admin_export_variant_keys, pattern="^admin_export_variant_keys_"))
    application.add_handler(CallbackQueryHandler(admin_delete_keys_menu, pattern="^admin_delete_keys_menu$"))
    application.add_handler(CallbackQueryHandler(admin_delete_keys_variant, pattern="^admin_delete_keys_prod_"))
    application.add_handler(CallbackQueryHandler(admin_confirm_delete_keys, pattern="^admin_confirm_delete_keys_"))
    application.add_handler(CallbackQueryHandler(admin_do_delete_keys, pattern="^admin_do_delete_keys_"))
    
    # Users
    application.add_handler(CallbackQueryHandler(admin_users_menu, pattern="^admin_users$"))
    application.add_handler(CallbackQueryHandler(admin_view_users, pattern="^admin_view_users$"))
    application.add_handler(CallbackQueryHandler(admin_block_user_prompt, pattern="^admin_block_user_prompt$"))
    
    # Broadcast
    application.add_handler(CallbackQueryHandler(admin_broadcast_menu, pattern="^admin_broadcast_menu$"))
    
    # Message handler for admin inputs (text and documents)
    # bKash TRX ID input handler (must be before admin handler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_bkash_txn_input))
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_message))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_admin_message))
    
    # Start bot
    await application.initialize()
    await application.start()
    await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    
    logger.info("✅ Bot started successfully!")
    
    # Start webhook server
    web_app = web.Application()
    web_app['bot'] = application.bot
    web_app.router.add_post('/webhook', payment_webhook_handler)
    web_app.router.add_post('/webhook/payerurl', payerurl_webhook_handler)
    web_app.router.add_post('/webhook/bkash', bkash_webhook_handler)
    web_app.router.add_get('/', health_check)
    
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    
    logger.info(f"✅ Webhook server on port {PORT}")
    logger.info("━━━━━━━━━━━━━━━━━━━")
    logger.info("🚀 SYSTEM FULLY OPERATIONAL!")
    logger.info("💎 UPI webhook: /webhook")
    logger.info("💎 PayerURL webhook: /webhook/payerurl")
    logger.info("✅ UPI, Crypto & bKash payments active!")
    logger.info("✅ Auto QR fetch enabled!")
    logger.info("✅ Auto-polling every 10 seconds!")
    logger.info("━━━━━━━━━━━━━━━━━━━")
    
    # Keep running
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")