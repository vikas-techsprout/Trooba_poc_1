import requests
import sqlite3
import os
import json
import time
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Shopify credentials from .env file
SHOP_NAME = os.getenv("SHOPIFY_SHOP_NAME")
ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
API_VERSION = "2023-10"  # Update to the latest stable version

# Define database path
DB_PATH = 'database/shopify_data.db'

def validate_credentials():
    """Validate Shopify credentials"""
    if not SHOP_NAME or not ACCESS_TOKEN:
        return False, "Missing Shopify credentials. Please check your .env file."
    
    if len(ACCESS_TOKEN) < 20:
        return False, "Access token appears to be invalid (too short)"
    
    # Basic shop name validation
    clean_shop_name = SHOP_NAME.lower().strip()
    if clean_shop_name.endswith('.myshopify.com'):
        clean_shop_name = clean_shop_name.replace('.myshopify.com', '')
    
    if not re.match(r'^[a-zA-Z0-9\-]+$', clean_shop_name):
        return False, "Shop name contains invalid characters. Use only letters, numbers, and hyphens."
    
    return True, None

def construct_base_url():
    """Construct the Shopify API base URL"""
    clean_shop_name = SHOP_NAME.lower().strip()
    if clean_shop_name.endswith('.myshopify.com'):
        clean_shop_name = clean_shop_name.replace('.myshopify.com', '')
    
    return f'https://{clean_shop_name}.myshopify.com/admin/api/{API_VERSION}'

def parse_link_header(link_header):
    """Parse Link header to extract next URL"""
    if not link_header or 'rel="next"' not in link_header:
        return None
    
    # Use regex to properly extract URL
    match = re.search(r'<([^>]+)>;\s*rel="next"', link_header)
    return match.group(1) if match else None

def setup_database():
    """Create the required tables for Shopify data"""
    # Create database directory if it doesn't exist
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    # Use context manager for proper connection handling
    with sqlite3.connect(DB_PATH, timeout=20) as conn:
        cursor = conn.cursor()
        
        # Create shopify_products table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS shopify_products (
            id INTEGER PRIMARY KEY,
            title TEXT,
            body_html TEXT,
            vendor TEXT,
            product_type TEXT,
            handle TEXT,
            status TEXT,
            tags TEXT,
            created_at TEXT,
            updated_at TEXT,
            published_at TEXT,
            raw_data TEXT
        )
        ''')
        
        # Create shopify_variants table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS shopify_variants (
            id INTEGER PRIMARY KEY,
            product_id INTEGER,
            title TEXT,
            price REAL,
            sku TEXT,
            position INTEGER,
            inventory_policy TEXT,
            compare_at_price REAL,
            inventory_management TEXT,
            option1 TEXT,
            option2 TEXT,
            option3 TEXT,
            created_at TEXT,
            updated_at TEXT,
            taxable BOOLEAN,
            barcode TEXT,
            inventory_item_id INTEGER,
            raw_data TEXT,
            FOREIGN KEY (product_id) REFERENCES shopify_products(id)
        )
        ''')
        
        # Create shopify_orders table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS shopify_orders (
            id INTEGER PRIMARY KEY,
            email TEXT,
            created_at TEXT,
            updated_at TEXT,
            number INTEGER,
            total_price REAL,
            subtotal_price REAL,
            total_tax REAL,
            currency TEXT,
            financial_status TEXT,
            fulfillment_status TEXT,
            processed_at TEXT,
            raw_data TEXT
        )
        ''')
        
        # Create shopify_order_line_items table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS shopify_order_line_items (
            id INTEGER PRIMARY KEY,
            order_id INTEGER,
            variant_id INTEGER,
            product_id INTEGER,
            title TEXT,
            variant_title TEXT,
            sku TEXT,
            quantity INTEGER,
            price REAL,
            total_discount REAL,
            created_at TEXT,
            raw_data TEXT,
            FOREIGN KEY (order_id) REFERENCES shopify_orders(id)
        )
        ''')
        
        # Create metadata table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS shopify_metadata (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            last_fetch_time TEXT,
            products_count INTEGER,
            orders_count INTEGER,
            status TEXT,
            error_message TEXT
        )
        ''')
        
        conn.commit()

def safe_get_value(obj, key, default=None, expected_type=None):
    """Safely get value from object with optional type conversion"""
    value = obj.get(key, default)
    
    if value is None or value == '':
        return default
    
    if expected_type:
        try:
            if expected_type == int:
                return int(float(value)) if value != '' else default
            elif expected_type == float:
                return float(value) if value != '' else default
            elif expected_type == bool:
                return bool(value)
            elif expected_type == str:
                return str(value)
        except (ValueError, TypeError):
            print(f"Warning: Could not convert {key}={value} to {expected_type}, using default")
            return default
    
    return value

def fetch_shopify_data():
    """Fetch data from Shopify and store in local database"""
    # Validate credentials
    is_valid, error_msg = validate_credentials()
    if not is_valid:
        update_metadata(status="error", error_message=error_msg)
        return {"success": False, "error": error_msg}
    
    # Create database tables if they don't exist
    try:
        setup_database()
    except Exception as e:
        error_msg = f"Database setup failed: {str(e)}"
        update_metadata(status="error", error_message=error_msg)
        return {"success": False, "error": error_msg}
    
    try:
        # Use context manager for database connection
        with sqlite3.connect(DB_PATH, timeout=20) as conn:
            cursor = conn.cursor()
            
            # Clear existing data
            cursor.execute("DELETE FROM shopify_order_line_items")
            cursor.execute("DELETE FROM shopify_orders")
            cursor.execute("DELETE FROM shopify_variants")
            cursor.execute("DELETE FROM shopify_products")
            
            # Construct the base URL (properly formatted)
            BASE_URL = construct_base_url()
            print(f"Connecting to Shopify API at: {BASE_URL}")
            
            # Set up the headers with authentication
            HEADERS = {
                'X-Shopify-Access-Token': ACCESS_TOKEN,
                'Content-Type': 'application/json'
            }
            
            # Test connection first with timeout
            test_url = f'{BASE_URL}/shop.json'
            try:
                test_response = requests.get(test_url, headers=HEADERS, timeout=30)
                test_response.raise_for_status()  # This will raise an exception for 4XX/5XX responses
                shop_data = test_response.json().get('shop', {})
                print(f"Successfully connected to Shopify store: {shop_data.get('name')}")
            except requests.exceptions.Timeout:
                error_msg = "Connection to Shopify API timed out after 30 seconds"
                print(error_msg)
                update_metadata(status="error", error_message=error_msg)
                return {"success": False, "error": error_msg}
            except requests.exceptions.RequestException as e:
                error_msg = f"Failed to connect to Shopify API: {str(e)}"
                print(error_msg)
                update_metadata(status="error", error_message=error_msg)
                return {"success": False, "error": error_msg}
            
            # Fetch products
            products_count = fetch_products(BASE_URL, HEADERS, cursor)
            
            # Fetch orders
            orders_count = fetch_orders(BASE_URL, HEADERS, cursor)
            
            # Update metadata
            update_metadata(
                status="success", 
                products_count=products_count, 
                orders_count=orders_count
            )
            
            conn.commit()
            return {
                "success": True, 
                "products_count": products_count, 
                "orders_count": orders_count
            }
            
    except requests.exceptions.RequestException as e:
        error_msg = f"Error fetching Shopify data: {str(e)}"
        print(error_msg)
        update_metadata(status="error", error_message=error_msg)
        return {"success": False, "error": error_msg}
    
    except sqlite3.Error as e:
        error_msg = f"Database error: {str(e)}"
        print(error_msg)
        update_metadata(status="error", error_message=error_msg)
        return {"success": False, "error": error_msg}
    
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        print(error_msg)
        update_metadata(status="error", error_message=error_msg)
        return {"success": False, "error": error_msg}

def fetch_products(base_url, headers, cursor):
    """Fetch products from Shopify API"""
    print("Fetching products from Shopify...")
    products_count = 0
    variants_count = 0
    
    try:
        # Get product data
        url = f'{base_url}/products.json?limit=250'
        
        while url:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            # Process each product
            for product in data.get('products', []):
                # Insert product data with safe value extraction
                cursor.execute('''
                INSERT OR REPLACE INTO shopify_products 
                (id, title, body_html, vendor, product_type, handle, status, tags, 
                created_at, updated_at, published_at, raw_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    safe_get_value(product, 'id', expected_type=int),
                    safe_get_value(product, 'title', ''),
                    safe_get_value(product, 'body_html', ''),
                    safe_get_value(product, 'vendor', ''),
                    safe_get_value(product, 'product_type', ''),
                    safe_get_value(product, 'handle', ''),
                    safe_get_value(product, 'status', ''),
                    safe_get_value(product, 'tags', ''),
                    safe_get_value(product, 'created_at', ''),
                    safe_get_value(product, 'updated_at', ''),
                    safe_get_value(product, 'published_at', ''),
                    json.dumps(product)
                ))
                
                products_count += 1
                
                # Process each variant
                for variant in product.get('variants', []):
                    # Insert variant data with safe value extraction
                    cursor.execute('''
                    INSERT OR REPLACE INTO shopify_variants
                    (id, product_id, title, price, sku, position, inventory_policy,
                    compare_at_price, inventory_management, option1, option2, option3,
                    created_at, updated_at, taxable, barcode, inventory_item_id, raw_data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        safe_get_value(variant, 'id', expected_type=int),
                        safe_get_value(product, 'id', expected_type=int),
                        safe_get_value(variant, 'title', ''),
                        safe_get_value(variant, 'price', 0.0, expected_type=float),
                        safe_get_value(variant, 'sku', ''),
                        safe_get_value(variant, 'position', 0, expected_type=int),
                        safe_get_value(variant, 'inventory_policy', ''),
                        safe_get_value(variant, 'compare_at_price', None, expected_type=float),
                        safe_get_value(variant, 'inventory_management', ''),
                        safe_get_value(variant, 'option1', ''),
                        safe_get_value(variant, 'option2', ''),
                        safe_get_value(variant, 'option3', ''),
                        safe_get_value(variant, 'created_at', ''),
                        safe_get_value(variant, 'updated_at', ''),
                        safe_get_value(variant, 'taxable', False, expected_type=bool),
                        safe_get_value(variant, 'barcode', ''),
                        safe_get_value(variant, 'inventory_item_id', None, expected_type=int),
                        json.dumps(variant)
                    ))
                    variants_count += 1
            
            # Check for pagination using improved parsing
            link_header = response.headers.get('Link', '')
            url = parse_link_header(link_header)
            
            # Respect API rate limits
            time.sleep(0.5)
        
        print(f"Fetched {products_count} products with {variants_count} variants")
        return products_count
    except Exception as e:
        print(f"Error fetching products: {e}")
        raise

def fetch_orders(base_url, headers, cursor, days=90):
    """Fetch orders from Shopify API"""
    print(f"Fetching orders from the last {days} days...")
    orders_count = 0
    line_items_count = 0
    
    # Calculate date for filtering orders
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    
    # Get orders with pagination
    url = f'{base_url}/orders.json?status=any&limit=250&created_at_min={start_date}'
    
    while url:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        # Process each order
        for order in data.get('orders', []):
            # Insert order data with safe value extraction
            cursor.execute('''
            INSERT OR REPLACE INTO shopify_orders
            (id, email, created_at, updated_at, number, total_price, subtotal_price, 
            total_tax, currency, financial_status, fulfillment_status, processed_at, raw_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                safe_get_value(order, 'id', expected_type=int),
                safe_get_value(order, 'email', ''),
                safe_get_value(order, 'created_at', ''),
                safe_get_value(order, 'updated_at', ''),
                safe_get_value(order, 'number', 0, expected_type=int),
                safe_get_value(order, 'total_price', 0.0, expected_type=float),
                safe_get_value(order, 'subtotal_price', 0.0, expected_type=float),
                safe_get_value(order, 'total_tax', 0.0, expected_type=float),
                safe_get_value(order, 'currency', ''),
                safe_get_value(order, 'financial_status', ''),
                safe_get_value(order, 'fulfillment_status', ''),
                safe_get_value(order, 'processed_at', ''),
                json.dumps(order)
            ))
            
            # Process line items
            for item in order.get('line_items', []):
                cursor.execute('''
                INSERT OR REPLACE INTO shopify_order_line_items
                (id, order_id, variant_id, product_id, title, variant_title,
                sku, quantity, price, total_discount, created_at, raw_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    safe_get_value(item, 'id', expected_type=int),
                    safe_get_value(order, 'id', expected_type=int),
                    safe_get_value(item, 'variant_id', None, expected_type=int),
                    safe_get_value(item, 'product_id', None, expected_type=int),
                    safe_get_value(item, 'title', ''),
                    safe_get_value(item, 'variant_title', ''),
                    safe_get_value(item, 'sku', ''),
                    safe_get_value(item, 'quantity', 0, expected_type=int),
                    safe_get_value(item, 'price', 0.0, expected_type=float),
                    safe_get_value(item, 'total_discount', 0.0, expected_type=float),
                    safe_get_value(order, 'created_at', ''),
                    json.dumps(item)
                ))
                line_items_count += 1
            
            orders_count += 1
        
        # Check for pagination using improved parsing
        link_header = response.headers.get('Link', '')
        url = parse_link_header(link_header)
        
        # Respect API rate limits
        time.sleep(0.5)
    
    print(f"Fetched {orders_count} orders with {line_items_count} line items")
    return orders_count

def update_metadata(status="unknown", products_count=0, orders_count=0, error_message=None):
    """Update metadata about the last fetch"""
    try:
        with sqlite3.connect(DB_PATH, timeout=20) as conn:
            cursor = conn.cursor()
            
            cursor.execute("DELETE FROM shopify_metadata")
            cursor.execute('''
            INSERT INTO shopify_metadata 
            (last_fetch_time, products_count, orders_count, status, error_message)
            VALUES (?, ?, ?, ?, ?)
            ''', (
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                products_count,
                orders_count,
                status,
                error_message
            ))
            
            conn.commit()
    except sqlite3.Error as e:
        print(f"Database error in update_metadata: {e}")

def check_shopify_data():
    """Check if we have Shopify data and return basic metrics"""
    try:
        with sqlite3.connect(DB_PATH, timeout=20) as conn:
            cursor = conn.cursor()
            
            # Check metadata first
            cursor.execute("SELECT * FROM shopify_metadata ORDER BY id DESC LIMIT 1")
            metadata = cursor.fetchone()
            
            if not metadata:
                return {
                    "has_data": False,
                    "error": "No metadata found. Data has not been fetched yet."
                }
            
            # Unpack metadata
            _, fetch_time, products_count, orders_count, status, error_message = metadata
            
            # If status is error, return the error
            if status == "error":
                return {
                    "has_data": False,
                    "error": error_message,
                    "fetch_time": fetch_time
                }
            
            # Check if we actually have products and orders
            cursor.execute("SELECT COUNT(*) FROM shopify_products")
            actual_products_count = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM shopify_orders")
            actual_orders_count = cursor.fetchone()[0]
            
            has_data = actual_products_count > 0 or actual_orders_count > 0
            
            return {
                "has_data": has_data,
                "fetch_time": fetch_time,
                "products_count": actual_products_count,
                "orders_count": actual_orders_count
            }
    
    except Exception as e:
        return {
            "has_data": False,
            "error": f"Error checking Shopify data: {str(e)}"
        }

def get_analytics():
    """Get basic analytics from Shopify data"""
    try:
        with sqlite3.connect(DB_PATH, timeout=20) as conn:
            conn.row_factory = sqlite3.Row  # This enables column access by name
            cursor = conn.cursor()
            
            # Check if we have data
            check_result = check_shopify_data()
            if not check_result.get("has_data", False):
                return {"has_data": False}
            
            # Calculate total sales
            cursor.execute("""
            SELECT SUM(total_price) as total_sales 
            FROM shopify_orders 
            WHERE financial_status != 'refunded'
            """)
            result = cursor.fetchone()
            total_sales = result['total_sales'] if result and result['total_sales'] is not None else 0
            
            # Calculate total orders
            cursor.execute("""
            SELECT COUNT(*) as total_orders 
            FROM shopify_orders 
            WHERE financial_status != 'refunded'
            """)
            result = cursor.fetchone()
            total_orders = result['total_orders'] if result else 0
            
            # Calculate total items sold
            cursor.execute("""
            SELECT SUM(quantity) as total_items 
            FROM shopify_order_line_items oli
            JOIN shopify_orders o ON oli.order_id = o.id
            WHERE o.financial_status != 'refunded'
            """)
            result = cursor.fetchone()
            total_items = result['total_items'] if result and result['total_items'] is not None else 0
            
            # Get top products by sales
            cursor.execute("""
            SELECT 
                p.title || CASE WHEN v.title != 'Default Title' AND v.title IS NOT NULL THEN ' - ' || v.title ELSE '' END as title,
                SUM(oli.quantity) as quantity,
                SUM(oli.quantity * oli.price) as sales
            FROM shopify_order_line_items oli
            JOIN shopify_orders o ON oli.order_id = o.id
            LEFT JOIN shopify_products p ON oli.product_id = p.id
            LEFT JOIN shopify_variants v ON oli.variant_id = v.id
            WHERE o.financial_status != 'refunded'
            GROUP BY oli.product_id, oli.variant_id
            ORDER BY sales DESC
            LIMIT 5
            """)
            top_products = [dict(row) for row in cursor.fetchall()]
            
            # Get product categories
            cursor.execute("""
            SELECT 
                COALESCE(product_type, 'Uncategorized') as category,
                COUNT(*) as count
            FROM shopify_products
            GROUP BY product_type
            ORDER BY count DESC
            """)
            categories = [dict(row) for row in cursor.fetchall()]
            
            return {
                "has_data": True,
                "total_sales": total_sales,
                "total_orders": total_orders,
                "total_items": total_items,
                "top_products": top_products,
                "categories": categories
            }
            
    except Exception as e:
        print(f"Error getting analytics: {e}")
        return {
            "has_data": False,
            "error": str(e)
        }

# Main execution when script is run directly
if __name__ == "__main__":
    print("Running Shopify data fetcher...")
    
    # Make sure database directory exists
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    # Delete existing database file to recreate it from scratch
    if os.path.exists(DB_PATH):
        try:
            print("Removing existing database file...")
            os.remove(DB_PATH)
            print("Database file removed. Will create a new one.")
        except Exception as e:
            print(f"Could not remove database file: {e}")
    
    result = fetch_shopify_data()
    if result["success"]:
        print(f"Successfully fetched {result['products_count']} products and {result['orders_count']} orders.")
    else:
        print(f"Failed to fetch data: {result['error']}")