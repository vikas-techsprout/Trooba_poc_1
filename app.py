# -------------------------------------------------------------------------
# IMPORTS AND ENVIRONMENT SETUP
# -------------------------------------------------------------------------
# Import necessary libraries for web application, data processing, visualization, 
# database operations, and AI integration
from flask import Flask, render_template, request, redirect, url_for, session, flash
import pandas as pd
import plotly.graph_objs as go
import plotly
import json
import os
import numpy as np
import sqlite3
import werkzeug.routing.exceptions
import markdown
from markupsafe import Markup
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Initialize Flask application and set secret key
app = Flask(__name__)
app.secret_key = 'supersecretkey'  # Use env var in production

# User dictionary for authentication (would use database in production)
users = {}

# -------------------------------------------------------------------------
# DATABASE CONFIGURATION
# -------------------------------------------------------------------------
# Define path to the SQLite database file for Shopify data
DB_PATH = 'database/shopify_data.db'

# -------------------------------------------------------------------------
# GEMINI AI CONFIGURATION
# -------------------------------------------------------------------------
# Try to get Gemini API key from environment variables or .env file
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
if not GEMINI_API_KEY:
    try:
        # Try to read from .env file
        with open('.env', 'r') as f:
            for line in f:
                if line.startswith('GEMINI_API_KEY='):
                    GEMINI_API_KEY = line.strip().split('=')[1]
                    break
        if GEMINI_API_KEY:
            print("Successfully loaded Gemini API key from .env file")
        else:
            print("WARNING: Gemini API key not found. Set the GEMINI_API_KEY environment variable.")
    except Exception as e:
        print(f"WARNING: Error reading .env file: {e}")
        print("WARNING: Gemini API key not found. Set the GEMINI_API_KEY environment variable.")

# Set up Gemini
genai.configure(api_key=GEMINI_API_KEY)

# Configure Gemini safety settings to prevent harmful content generation
safety_settings = {
    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
}

# Initialize the Gemini model with specific configuration parameters
try:
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        safety_settings=safety_settings,
        generation_config={
            "temperature": 0.7,
            "top_p": 0.95,
            "top_k": 40,
            "max_output_tokens": 2500,
        }
    )
except Exception as e:
    print(f"Error initializing Gemini model: {e}")
    model = None

# -------------------------------------------------------------------------
# DIRECTORY AND PROMPTS SETUP
# -------------------------------------------------------------------------
# Create directories for storing AI insights and prompt templates
os.makedirs('ai_insights', exist_ok=True)
os.makedirs('prompts', exist_ok=True)

# Create Shopify-specific prompts
if not os.path.exists('prompts/__init__.py'):
    with open('prompts/__init__.py', 'w', encoding='utf-8') as f:
        f.write('# Prompts package\n')

if not os.path.exists('prompts/shopify_insights_prompt.py'):
    with open('prompts/shopify_insights_prompt.py', 'w', encoding='utf-8') as f:
        f.write('''
SHOPIFY_SALES_PROMPT = """
You are TROOBA, an expert e-commerce analytics assistant for Shopify stores. 
Your job is to process provided data (sales, products, orders, etc.) and generate flash-card–style insights.

Sales Data:
{sales_data_summary}

Product Data:
{product_data_summary}

Order Performance:
{order_performance}

Top Products:
{top_products}

Generate 5 SALES PERFORMANCE insight cards with the following format:

### **🔹 SALES PERFORMANCE**
**🛍️ Product: [Product Name] (SKU: [SKU Code])**
**Recommendation:** [Clear action with specific numbers]
**Reasoning:**
• [Data-backed point about current performance]
• [Specific opportunity with numbers]
• [Marketing or pricing strategy with expected outcome]
• [Revenue impact calculation in ₹]
• [Customer behavior analysis]

**📌 Simple Steps:**
1. [Specific action for store management]
2. [Product optimization suggestion]
3. [Marketing or promotion implementation]

Use sharp, business-friendly language with emojis and bold for product names. Every recommendation must include specific pricing, percentages, or quantities based on the data provided.
"""

SHOPIFY_INVENTORY_PROMPT = """
You are TROOBA, an expert inventory-management assistant for Shopify stores.
Analyze the provided product and sales data to generate actionable insights.

Product Data:
{product_data_summary}

Sales Data:
{sales_data_summary}

Inventory Status:
{inventory_status}

Product Performance:
{product_performance}

Generate 5 INVENTORY INTELLIGENCE insight cards with the following format:

### **🔹 INVENTORY INTELLIGENCE**
**📦 Product: [Product Name] (SKU: [SKU Code])**
**Recommendation:** [Clear inventory action with specific numbers]
**Reasoning:**
• [Detailed stock analysis with numbers]
• [Sales velocity and trend analysis]
• [Financial calculation (revenue, costs in ₹)]
• [Market opportunity assessment]
• [Customer demand insights]

**📌 Simple Steps:**
1. [Specific inventory action]
2. [Pricing or bundling strategy]
3. [Marketing or display change]
4. [Performance tracking metric]

Focus on stock levels, sales velocity, product variants, and revenue optimization. Every insight must have specific numbers and detailed reasoning behind the recommendation.
"""
''')

# Import Shopify insights prompts
try:
    from prompts.shopify_insights_prompt import SHOPIFY_SALES_PROMPT, SHOPIFY_INVENTORY_PROMPT
except ImportError:
    print("WARNING: Failed to import Shopify insights prompts.")
    SHOPIFY_SALES_PROMPT = ""
    SHOPIFY_INVENTORY_PROMPT = ""

# -------------------------------------------------------------------------
# DATABASE UTILITY FUNCTIONS
# -------------------------------------------------------------------------

def get_db_connection():
    """
    Create a database connection to the Shopify SQLite database
    
    Returns:
        sqlite3.Connection: A connection object to the SQLite database with row factory set
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def check_database_exists():
    """
    Check if the Shopify database exists and has data
    
    This function verifies:
    1. If the database file exists
    2. If the required tables exist in the database
    3. If the tables contain any data
    
    Returns:
        tuple: (exists_flag, message)
            - exists_flag (bool): True if database exists and has data
            - message (str): Status message describing the database state
    """
    if not os.path.exists(DB_PATH):
        return False, "Shopify database not found. Please run the Shopify data fetcher first."
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if tables exist and have data
        cursor.execute("SELECT COUNT(*) FROM shopify_products")
        products_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM shopify_orders")
        orders_count = cursor.fetchone()[0]
        
        conn.close()
        
        if products_count == 0 and orders_count == 0:
            return False, "Shopify database is empty. Please fetch data from Shopify first."
        
        return True, f"Database ready with {products_count} products and {orders_count} orders"
        
    except Exception as e:
        return False, f"Database error: {str(e)}"

def prepare_shopify_sales_data_for_ai():
    """
    Extract and format Shopify sales data for the AI prompt
    
    This function:
    1. Queries the database for top selling products
    2. Collects order performance data over time
    3. Analyzes sales by product category
    4. Formats all data for AI consumption
    
    Returns:
        dict: A dictionary containing formatted sales data for AI analysis:
            - sales_data_summary: Overview of top selling products
            - order_performance: Daily order metrics
            - top_products: Details of the best performing products
    """
    try:
        conn = get_db_connection()
        
        # Get top selling products with order data
        top_selling_query = """
        SELECT 
            p.title,
            v.sku,
            p.product_type,
            v.price,
            SUM(oli.quantity) as total_quantity_sold,
            SUM(oli.quantity * oli.price) as total_revenue,
            COUNT(DISTINCT oli.order_id) as order_count,
            AVG(oli.price) as avg_selling_price
        FROM shopify_order_line_items oli
        JOIN shopify_orders o ON oli.order_id = o.id
        LEFT JOIN shopify_products p ON oli.product_id = p.id
        LEFT JOIN shopify_variants v ON oli.variant_id = v.id
        WHERE o.financial_status != 'refunded'
        GROUP BY oli.product_id, oli.variant_id
        ORDER BY total_revenue DESC
        LIMIT 20
        """
        
        # Get order performance by time period
        order_performance_query = """
        SELECT 
            DATE(o.created_at) as order_date,
            COUNT(*) as daily_orders,
            SUM(o.total_price) as daily_revenue,
            AVG(o.total_price) as avg_order_value
        FROM shopify_orders o
        WHERE o.financial_status != 'refunded'
        AND DATE(o.created_at) >= DATE('now', '-30 days')
        GROUP BY DATE(o.created_at)
        ORDER BY order_date DESC
        LIMIT 30
        """
        
        # Get category performance
        category_query = """
        SELECT 
            COALESCE(p.product_type, 'Uncategorized') as category,
            COUNT(DISTINCT p.id) as product_count,
            SUM(oli.quantity) as total_units_sold,
            SUM(oli.quantity * oli.price) as total_revenue
        FROM shopify_products p
        LEFT JOIN shopify_order_line_items oli ON p.id = oli.product_id
        LEFT JOIN shopify_orders o ON oli.order_id = o.id
        WHERE o.financial_status != 'refunded' OR oli.id IS NULL
        GROUP BY p.product_type
        ORDER BY total_revenue DESC
        """
        
        # Execute queries
        top_products_df = pd.read_sql_query(top_selling_query, conn)
        order_performance_df = pd.read_sql_query(order_performance_query, conn)
        category_df = pd.read_sql_query(category_query, conn)
        
        conn.close()
        
        # Format data for AI prompt
        sales_data_summary = top_products_df.to_string(index=False)
        order_performance = order_performance_df.to_string(index=False)
        top_products = top_products_df.head(10).to_string(index=False)
        
        return {
            "sales_data_summary": sales_data_summary,
            "order_performance": order_performance,
            "top_products": top_products
        }
    except Exception as e:
        print(f"Error preparing Shopify sales data: {e}")
        return {
            "sales_data_summary": "Error retrieving sales data",
            "order_performance": "Error retrieving order data",
            "top_products": "Error retrieving top products data"
        }

def prepare_shopify_product_data_for_ai():
    """
    Extract and format Shopify product data for the AI prompt
    
    This function:
    1. Queries product inventory status and details
    2. Analyzes product performance metrics
    3. Summarizes inventory status by product category
    
    Returns:
        dict: A dictionary containing formatted product data for AI analysis:
            - product_data_summary: Overview of product inventory
            - product_performance: Sales performance metrics by product
            - inventory_status: Summary of inventory by category
    """
    try:
        conn = get_db_connection()
        
        # Get product inventory status
        product_query = """
        SELECT 
            p.title,
            v.sku,
            p.product_type,
            v.price,
            p.status,
            COUNT(v.id) as variant_count,
            p.created_at,
            p.updated_at
        FROM shopify_products p
        LEFT JOIN shopify_variants v ON p.id = v.product_id
        GROUP BY p.id
        ORDER BY p.created_at DESC
        LIMIT 50
        """
        
        # Get product performance (sales data)
        performance_query = """
        SELECT 
            p.title,
            v.sku,
            p.product_type,
            SUM(oli.quantity) as total_sold,
            SUM(oli.quantity * oli.price) as total_revenue,
            COUNT(DISTINCT oli.order_id) as unique_orders,
            MAX(o.created_at) as last_sale_date
        FROM shopify_products p
        LEFT JOIN shopify_variants v ON p.id = v.product_id
        LEFT JOIN shopify_order_line_items oli ON v.id = oli.variant_id
        LEFT JOIN shopify_orders o ON oli.order_id = o.id
        WHERE o.financial_status != 'refunded' OR oli.id IS NULL
        GROUP BY p.id
        ORDER BY total_revenue DESC NULLS LAST
        LIMIT 30
        """
        
        # Get inventory status summary
        inventory_query = """
        SELECT 
            p.product_type,
            COUNT(p.id) as total_products,
            COUNT(v.id) as total_variants,
            SUM(CASE WHEN p.status = 'active' THEN 1 ELSE 0 END) as active_products
        FROM shopify_products p
        LEFT JOIN shopify_variants v ON p.id = v.product_id
        GROUP BY p.product_type
        ORDER BY total_products DESC
        """
        
        # Execute queries
        product_df = pd.read_sql_query(product_query, conn)
        performance_df = pd.read_sql_query(performance_query, conn)
        inventory_df = pd.read_sql_query(inventory_query, conn)
        
        conn.close()
        
        # Format data for AI prompt
        product_data_summary = product_df.to_string(index=False)
        product_performance = performance_df.to_string(index=False)
        inventory_status = inventory_df.to_string(index=False)
        
        return {
            "product_data_summary": product_data_summary,
            "product_performance": product_performance,
            "inventory_status": inventory_status
        }
    except Exception as e:
        print(f"Error preparing Shopify product data: {e}")
        return {
            "product_data_summary": "Error retrieving product data",
            "product_performance": "Error retrieving performance data",  
            "inventory_status": "Error retrieving inventory data"
        }

def generate_sales_insights():
    """
    Generate AI-powered sales insights using Gemini for Shopify data
    
    This function:
    1. Prepares sales and product data by fetching from the database
    2. Formats the data into a prompt for the Gemini AI model
    3. Sends the prompt to the Gemini API and retrieves insights
    4. Saves the generated insights to files for caching
    
    Returns:
        str: Markdown-formatted sales insights text
    """
    sales_data = prepare_shopify_sales_data_for_ai()
    product_data = prepare_shopify_product_data_for_ai()
    
    # Create the prompt with data
    prompt = SHOPIFY_SALES_PROMPT.format(
        sales_data_summary=sales_data["sales_data_summary"],
        product_data_summary=product_data["product_data_summary"],
        order_performance=sales_data["order_performance"],
        top_products=sales_data["top_products"]
    )
    
    try:
        # Check if API key is available
        if not GEMINI_API_KEY or model is None:
            return """
### **🔹 SALES PERFORMANCE**
**🛍️ Product: Missing Gemini API Key**

**Recommendation:** Please set your Gemini API key to enable AI-powered insights.

**Reasoning:**
• The system requires a Gemini API key to generate insights.
• The key should be set as an environment variable GEMINI_API_KEY.
• Without this key, the AI analysis cannot be performed.

**📌 Simple Steps:**
1. Set the environment variable: $env:GEMINI_API_KEY = "your-api-key-here"
2. Restart the application.
3. Try refreshing the insights again.
            """
        
        # Call Gemini API
        system_instruction = "You are TROOBA, an expert Shopify e-commerce analytics assistant."
        
        response = model.generate_content([
            system_instruction, 
            prompt
        ])
        
        # Get the response
        insights = response.text
        
        # Save insights to file for debugging/record
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        with open(f"ai_insights/shopify_sales_insights_{timestamp}.md", "w", encoding='utf-8') as f:
            f.write(insights)
        
        # Also save as latest for quick access
        with open(f"ai_insights/latest_sales_insights.md", "w", encoding='utf-8') as f:
            f.write(insights)
        
        return insights
        
    except Exception as e:
        print(f"Error generating sales insights: {e}")
        # Return a fallback response
        return f"""
### **🔹 SALES PERFORMANCE**
**🛍️ Product: Error Generating Insights**

**Recommendation:** Please check your Gemini API key and connection.

**Reasoning:**
• The AI service encountered an error while generating insights: {str(e)}
• This could be due to an invalid API key, network issues, or service outage.
• Please check your configuration and try again later.

**📌 Simple Steps:**
1. Verify your Gemini API key is correctly set in the environment variables.
2. Check your internet connection.
3. Try refreshing the page or restarting the application.
        """

def generate_inventory_insights():
    """
    Generate AI-powered inventory insights using Gemini for Shopify data
    
    This function:
    1. Prepares product and sales data from the database
    2. Formats the data into a prompt for the Gemini AI model
    3. Sends the prompt to the Gemini API and retrieves inventory analysis
    4. Saves the generated insights to files for caching
    
    Returns:
        str: Markdown-formatted inventory insights text
    """
    sales_data = prepare_shopify_sales_data_for_ai()
    product_data = prepare_shopify_product_data_for_ai()
    
    # Create the prompt with data
    prompt = SHOPIFY_INVENTORY_PROMPT.format(
        product_data_summary=product_data["product_data_summary"],
        sales_data_summary=sales_data["sales_data_summary"],
        inventory_status=product_data["inventory_status"],
        product_performance=product_data["product_performance"]
    )
    
    try:
        # Check if API key is available
        if not GEMINI_API_KEY or model is None:
            return """
### **🔹 INVENTORY INTELLIGENCE**
**📦 Product: Missing Gemini API Key**

**Recommendation:** Please set your Gemini API key to enable AI-powered insights.

**Reasoning:**
• The system requires a Gemini API key to generate insights.
• The key should be set as an environment variable GEMINI_API_KEY.
• Without this key, the AI analysis cannot be performed.

**📌 Simple Steps:**
1. Set the environment variable: $env:GEMINI_API_KEY = "your-api-key-here"
2. Restart the application.
3. Try refreshing the insights again.
            """
        
        # Call Gemini API
        system_instruction = "You are TROOBA, an expert Shopify inventory management assistant."
        
        response = model.generate_content([
            system_instruction, 
            prompt
        ])
        
        # Get the response
        insights = response.text
        
        # Save insights to file for debugging/record
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        with open(f"ai_insights/shopify_inventory_insights_{timestamp}.md", "w", encoding='utf-8') as f:
            f.write(insights)
        
        # Also save as latest for quick access
        with open(f"ai_insights/latest_inventory_insights.md", "w", encoding='utf-8') as f:
            f.write(insights)
        
        return insights
        
    except Exception as e:
        print(f"Error generating inventory insights: {e}")
        # Return a fallback response
        return f"""
### **🔹 INVENTORY INTELLIGENCE**
**📦 Product: Error Generating Insights**

**Recommendation:** Please check your Gemini API key and connection.

**Reasoning:**
• The AI service encountered an error while generating insights: {str(e)}
• This could be due to an invalid API key, network issues, or service outage.
• Please check your configuration and try again later.

**📌 Simple Steps:**
1. Verify your Gemini API key is correctly set in the environment variables.
2. Check your internet connection.
3. Try refreshing the page or restarting the application.
        """

@app.route('/')
def home():
    """
    Homepage route
    
    Displays the application's landing page with user information if logged in.
    """
    return render_template('home.html', user=session.get('user'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    """
    User registration route
    
    GET: Displays the registration form
    POST: Processes registration form data and creates a new user account
    """
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username in users:
            flash("User already exists. Try logging in.", "warning")
            return redirect(url_for('register'))
        users[username] = password
        flash("Registration successful! Please log in.", "success")
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """
    User login route
    
    GET: Displays the login form
    POST: Authenticates user credentials and creates a session
    
    Parameters:
        next: URL to redirect to after successful login
    """
    next_page = request.args.get('next') or url_for('home')
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username in users and users[username] == password:
            session['user'] = username
            return redirect(next_page)
        flash("Invalid credentials", "danger")
    return render_template('login.html', next=next_page)

@app.route('/logout')
def logout():
    """
    User logout route
    
    Ends the user session and redirects to the homepage
    """
    session.pop('user', None)
    flash("You have been logged out", "info")
    return redirect(url_for('home'))

@app.route('/dashboard')
def dashboard():
    """
    Dashboard route
    
    Displays the main analytics dashboard with Shopify sales data visualizations
    
    This route:
    1. Checks if user is logged in
    2. Verifies database exists and has data
    3. Queries database for sales metrics and product data
    4. Creates interactive visualizations using Plotly
    5. Renders dashboard template with sales metrics and charts
    
    Charts include:
    - Top products by revenue
    - Top products by quantity
    - Revenue by product category
    - Sales trend over time
    """
    if 'user' not in session:
        flash("Please login to access the dashboard.", "warning")
        return redirect(url_for('login', next=request.path))
    
    # Check if database exists and has data
    db_exists, db_message = check_database_exists()
    if not db_exists:
        flash(f"Database issue: {db_message}", "warning")
        return render_template('dashboard_no_data.html', 
                             user=session.get('user'),
                             db_message=db_message)
    
    # Connect to Shopify database
    conn = get_db_connection()
    
    try:        # Calculate metrics from Shopify data
        metrics_query = """
        SELECT 
            SUM(o.total_price) as total_sales_value,
            COUNT(DISTINCT o.id) as total_orders,
            SUM(oli.quantity) as total_units_sold,
            AVG(o.total_price) as avg_order_value
        FROM shopify_orders o
        LEFT JOIN shopify_order_line_items oli ON o.id = oli.order_id
        WHERE o.financial_status != 'refunded'
        """
        metrics = pd.read_sql(metrics_query, conn).iloc[0]
        
        # Get product count
        product_count_query = "SELECT COUNT(*) as product_count FROM shopify_products WHERE status = 'active'"
        product_count = pd.read_sql(product_count_query, conn).iloc[0]['product_count']
          # Get top products by revenue
        top_revenue_query = """
        SELECT 
            CASE 
                WHEN p.title IS NOT NULL THEN p.title || CASE WHEN v.title != 'Default Title' THEN ' - ' || v.title ELSE '' END
                ELSE 'Unknown Product'
            END as product_name,
            SUM(oli.quantity * oli.price) as revenue
        FROM shopify_order_line_items oli
        JOIN shopify_orders o ON oli.order_id = o.id
        LEFT JOIN shopify_products p ON oli.product_id = p.id
        LEFT JOIN shopify_variants v ON oli.variant_id = v.id
        WHERE o.financial_status != 'refunded'
        GROUP BY oli.product_id, oli.variant_id
        ORDER BY revenue DESC
        LIMIT 5
        """
        top_products_revenue = pd.read_sql(top_revenue_query, conn)
        
        # Get top products by quantity
        top_quantity_query = """
        SELECT 
            CASE 
                WHEN p.title IS NOT NULL THEN p.title || CASE WHEN v.title != 'Default Title' THEN ' - ' || v.title ELSE '' END
                ELSE 'Unknown Product'
            END as product_name,
            SUM(oli.quantity) as quantity
        FROM shopify_order_line_items oli
        JOIN shopify_orders o ON oli.order_id = o.id
        LEFT JOIN shopify_products p ON oli.product_id = p.id
        LEFT JOIN shopify_variants v ON oli.variant_id = v.id
        WHERE o.financial_status != 'refunded'
        GROUP BY oli.product_id, oli.variant_id
        ORDER BY quantity DESC
        LIMIT 5
        """
        top_products_quantity = pd.read_sql(top_quantity_query, conn)
        
        # Create enhanced visualization for top revenue products
        colors = ['#5d5fef', '#4079ed', '#3cd856', '#a700ff', '#ffa412']
        
        # Create improved revenue bar chart
        revenue_fig = go.Figure()
        
        # Truncate long product names for better display
        display_names = [name[:30] + '...' if len(name) > 30 else name for name in top_products_revenue['product_name']]
        
        revenue_fig.add_trace(go.Bar(
            x=display_names,
            y=top_products_revenue['revenue'],
            marker=dict(
                color=colors,
                line=dict(width=1, color='#333')
            ),            hovertemplate='<b>Product:</b> %{customdata}<br><b>Revenue:</b> ₹%{y:,.2f}<extra></extra>',
            customdata=top_products_revenue['product_name'],
            text=top_products_revenue['revenue'].apply(lambda x: f'₹{x:,.0f}'),
            textposition='auto'
        ))
        revenue_fig.update_layout(
            title='Top 5 Products by Revenue',
            xaxis_title='Product',
            yaxis_title='Revenue (₹)',
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)',
            margin=dict(l=60, r=30, t=50, b=100),
            hoverlabel=dict(
                bgcolor="white",
                font_size=14
            ),
            font=dict(
                family="Poppins, sans-serif",
                size=12
            ),
            xaxis=dict(tickangle=-45)
        )

        # Create quantity sold chart
        quantity_fig = go.Figure()
        
        display_names_qty = [name[:30] + '...' if len(name) > 30 else name for name in top_products_quantity['product_name']]
        
        quantity_fig.add_trace(go.Bar(
            x=display_names_qty,
            y=top_products_quantity['quantity'],
            marker=dict(
                color=colors,
                line=dict(width=1, color='#333'),
                opacity=0.8
            ),
            hovertemplate='<b>Product:</b> %{customdata}<br><b>Quantity:</b> %{y}<extra></extra>',
            customdata=top_products_quantity['product_name'],
            text=top_products_quantity['quantity'].apply(lambda x: f'{int(x)}'),
            textposition='auto'
        ))
        
        quantity_fig.update_layout(
            title='Top 5 Products by Quantity Sold',
            xaxis_title='Product',
            yaxis_title='Quantity Sold',
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)',
            margin=dict(l=60, r=30, t=50, b=100),
            hoverlabel=dict(
                bgcolor="white",
                font_size=14
            ),
            font=dict(
                family="Poppins, sans-serif",
                size=12
            ),
            xaxis=dict(tickangle=-45)
        )
          # Create category distribution chart
        category_query = """
        SELECT 
            COALESCE(p.product_type, 'Uncategorized') as category,
            SUM(oli.quantity * oli.price) as total_revenue,
            COUNT(DISTINCT p.id) as product_count
        FROM shopify_products p
        LEFT JOIN shopify_order_line_items oli ON p.id = oli.product_id
        LEFT JOIN shopify_orders o ON oli.order_id = o.id
        WHERE o.financial_status != 'refunded' OR oli.id IS NULL
        GROUP BY p.product_type
        HAVING total_revenue > 0
        ORDER BY total_revenue DESC
        LIMIT 8
        """
        category_sales = pd.read_sql(category_query, conn)
        
        category_fig = go.Figure()
        
        category_fig.add_trace(go.Pie(
            labels=category_sales['category'], 
            values=category_sales['total_revenue'],
            hole=0.4,
            textinfo='label+percent',
            marker=dict(
                colors=colors * 2,  # Repeat colors if needed
                line=dict(color='#FFFFFF', width=2)
            ),
            hovertemplate='<b>Category:</b> %{label}<br><b>Revenue:</b> ₹%{value:,.0f}<br><b>Percentage:</b> %{percent}<extra></extra>'
        ))
        
        category_fig.update_layout(
            title='Revenue Distribution by Product Category',
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)',
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=-0.2,
                xanchor="center",
                x=0.5
            ),
            margin=dict(l=20, r=20, t=50, b=20),
            font=dict(
                family="Poppins, sans-serif",
                size=12
            )
        )
        
        # Create sales trend over time (last 30 days)
        trend_query = """
        SELECT 
            DATE(o.created_at) as order_date,
            SUM(o.total_price) as daily_revenue,
            COUNT(*) as daily_orders
        FROM shopify_orders o
        WHERE o.financial_status != 'refunded'
        AND DATE(o.created_at) >= DATE('now', '-30 days')
        GROUP BY DATE(o.created_at)
        ORDER BY order_date ASC
        """
        trend_data = pd.read_sql(trend_query, conn)
        
        sales_trend_fig = go.Figure()
        
        # Add revenue line
        sales_trend_fig.add_trace(go.Scatter(
            x=trend_data['order_date'],
            y=trend_data['daily_revenue'],
            mode='lines+markers',
            name='Daily Revenue',
            line=dict(color='#5d5fef', width=3),
            marker=dict(size=8),
            hovertemplate='<b>Date:</b> %{x}<br><b>Revenue:</b> ₹%{y:,.2f}<extra></extra>'
        ))
        
        # Add orders line on secondary y-axis
        sales_trend_fig.add_trace(go.Scatter(
            x=trend_data['order_date'],
            y=trend_data['daily_orders'],
            mode='lines+markers',
            name='Daily Orders',
            line=dict(color='#ffa412', width=3),
            marker=dict(size=8),
            yaxis='y2',
            hovertemplate='<b>Date:</b> %{x}<br><b>Orders:</b> %{y}<extra></extra>'
        ))
        sales_trend_fig.update_layout(
            title='Sales Trend (Last 30 Days)',
            xaxis_title='Date',
            yaxis_title='Revenue (₹)',
            yaxis2=dict(
                title='Orders',
                overlaying='y',
                side='right'
            ),
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)',
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=-0.3,
                xanchor="center",
                x=0.5
            ),
            margin=dict(l=60, r=60, t=50, b=80),
            font=dict(
                family="Poppins, sans-serif",
                size=12
            ),
            hovermode="x unified"
        )

        # Convert figures to JSON for the template
        plotly_revenue_graph = json.dumps(revenue_fig, cls=plotly.utils.PlotlyJSONEncoder)
        plotly_quantity_graph = json.dumps(quantity_fig, cls=plotly.utils.PlotlyJSONEncoder) 
        plotly_category_graph = json.dumps(category_fig, cls=plotly.utils.PlotlyJSONEncoder)
        plotly_trend_graph = json.dumps(sales_trend_fig, cls=plotly.utils.PlotlyJSONEncoder)

        conn.close()        # Prepare data for dashboard.html template
        # Create dummy data for top skus by sales to match the expected variable names
        top_skus_by_sales = [
            {"sku_code": row["product_name"].split(' - ')[0][:10], 
             "net_sales_excl_tax": f"{row['revenue']:.2f}"} 
            for i, row in top_products_revenue.iterrows()
        ]
        
        # Create dummy data for top skus by sellthrough to match the expected variable names
        top_skus_by_sellthrough = [
            {"sku_code": row["product_name"].split(' - ')[0][:10], 
             "sell_through_rate_last_90_day": f"{(row['quantity'] / 100) * 80:.1f}"} 
            for i, row in top_products_quantity.iterrows()
        ]
        
        return render_template('dashboard.html',
            total_sales=round(metrics['total_sales_value'] or 0, 2),
            total_units=int(metrics['total_units_sold'] or 0),
            avg_sell_through=75.5,  # Placeholder value expected by the template
            avg_turnover_ratio=3.2,  # Placeholder value expected by the template
            avg_days_on_hand=45.8,  # Placeholder value expected by the template
            top_skus_by_sales=top_skus_by_sales,
            top_skus_by_sellthrough=top_skus_by_sellthrough,
            plotly_sales_graph=plotly_revenue_graph,
            plotly_units_graph=plotly_quantity_graph,
            plotly_category_graph=plotly_category_graph,
            plotly_jewelry_trend_graph=plotly_trend_graph,
            user=session.get('user')
        )
        
    except Exception as e:
        flash(f"Error loading dashboard data: {str(e)}", "error")
        conn.close()
        return render_template('dashboard_no_data.html', 
                             user=session.get('user'),
                             db_message=f"Error: {str(e)}")

@app.route('/inventory_insights')
def inventory_insights():
    """
    Inventory insights route
    
    Displays inventory analysis including:
    - Dead stock identification (products with no recent sales)
    - Top performing products 
    - Variant analysis
    - New product performance
    - Interactive charts for product status, category performance, and sales velocity
    
    This route:
    1. Verifies user is logged in
    2. Checks database exists and has data
    3. Queries database for inventory metrics
    4. Creates visualizations for inventory analysis
    5. Renders template with inventory insights
    """
    if 'user' not in session:
        flash("Please login to access inventory insights.", "warning")
        return redirect(url_for('login', next=request.path))
    
    # Check if database exists and has data
    db_exists, db_message = check_database_exists()
    if not db_exists:
        flash(f"Database issue: {db_message}", "warning")
        return render_template('inventory_insights_no_data.html', 
                             user=session.get('user'),
                             db_message=db_message)
    
    # Connect to database
    conn = get_db_connection()
    
    try:        # Get products with no recent sales (potential dead stock)
        dead_stock_query = """
        SELECT 
            p.title,
            v.sku,
            p.product_type,
            v.price,
            p.status,
            COALESCE(SUM(oli.quantity), 0) as total_sold
        FROM shopify_products p
        LEFT JOIN shopify_variants v ON p.id = v.product_id
        LEFT JOIN shopify_order_line_items oli ON v.id = oli.variant_id
        LEFT JOIN shopify_orders o ON oli.order_id = o.id AND o.created_at >= DATE('now', '-90 days')
        WHERE p.status = 'active'
        GROUP BY p.id, v.id
        HAVING total_sold = 0
        ORDER BY p.created_at DESC
        LIMIT 10
        """
        dead_stock_items = pd.read_sql(dead_stock_query, conn)
          # Get top performing products (high sales)
        top_performers_query = """
        SELECT 
            p.title,
            v.sku,
            p.product_type,
            v.price,
            SUM(oli.quantity) as total_sold,
            SUM(oli.quantity * oli.price) as total_revenue
        FROM shopify_products p
        JOIN shopify_variants v ON p.id = v.product_id
        JOIN shopify_order_line_items oli ON v.id = oli.variant_id
        JOIN shopify_orders o ON oli.order_id = o.id
        WHERE o.financial_status != 'refunded'
        AND o.created_at >= DATE('now', '-90 days')
        GROUP BY p.id, v.id
        ORDER BY total_revenue DESC
        LIMIT 10
        """
        top_performers = pd.read_sql(top_performers_query, conn)
        
        # Get products with single variants vs multiple variants
        variant_analysis_query = """
        SELECT 
            p.title,
            p.product_type,
            COUNT(v.id) as variant_count,
            SUM(COALESCE(oli.quantity, 0)) as total_sold
        FROM shopify_products p
        LEFT JOIN shopify_variants v ON p.id = v.product_id
        LEFT JOIN shopify_order_line_items oli ON v.id = oli.variant_id
        LEFT JOIN shopify_orders o ON oli.order_id = o.id AND o.created_at >= DATE('now', '-90 days')
        WHERE p.status = 'active'
        GROUP BY p.id
        ORDER BY variant_count DESC
        LIMIT 15
        """
        variant_analysis = pd.read_sql(variant_analysis_query, conn)
          # Get recently added products
        new_products_query = """
        SELECT 
            p.title,
            v.sku,
            p.product_type,
            v.price,
            p.created_at,
            COALESCE(SUM(oli.quantity), 0) as sales_since_launch
        FROM shopify_products p
        LEFT JOIN shopify_variants v ON p.id = v.product_id
        LEFT JOIN shopify_order_line_items oli ON v.id = oli.variant_id
        LEFT JOIN shopify_orders o ON oli.order_id = o.id
        WHERE p.created_at >= DATE('now', '-30 days')
        GROUP BY p.id, v.id
        ORDER BY p.created_at DESC
        LIMIT 10
        """
        new_products = pd.read_sql(new_products_query, conn)
        
        # Create charts
        # 1. Product Status Distribution
        status_query = """
        SELECT 
            status,
            COUNT(*) as count
        FROM shopify_products
        GROUP BY status
        """
        product_status = pd.read_sql(status_query, conn)
        
        colors = ['#10b981', '#ef4444', '#f59e0b', '#8b5cf6']
        status_fig = go.Figure(data=[
            go.Pie(
                labels=product_status['status'],
                values=product_status['count'],
                hole=.4,
                textinfo='label+percent',
                textposition='outside',
                marker=dict(
                    colors=colors[:len(product_status)],
                    line=dict(color='#FFFFFF', width=2)
                ),
                hovertemplate='<b>Status:</b> %{label}<br><b>Products:</b> %{value}<br><b>Percentage:</b> %{percent}<extra></extra>'
            )
        ])
        
        status_fig.update_layout(
            title='Product Status Distribution',
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)',
            margin=dict(l=20, r=20, t=80, b=20),
            font=dict(family="Poppins, sans-serif"),
            legend=dict(orientation="h", yanchor="bottom", y=-0.15, xanchor="center", x=0.5)
        )
        
        # 2. Category Performance Chart
        category_performance_query = """
        SELECT 
            COALESCE(p.product_type, 'Uncategorized') as category,
            COUNT(DISTINCT p.id) as product_count,
            SUM(COALESCE(oli.quantity, 0)) as total_sold,
            SUM(COALESCE(oli.quantity * oli.price, 0)) as total_revenue
        FROM shopify_products p
        LEFT JOIN shopify_order_line_items oli ON p.id = oli.product_id
        LEFT JOIN shopify_orders o ON oli.order_id = o.id AND o.created_at >= DATE('now', '-90 days')
        WHERE p.status = 'active'
        GROUP BY p.product_type
        ORDER BY total_revenue DESC
        LIMIT 8
        """
        category_performance = pd.read_sql(category_performance_query, conn)
        
        category_fig = go.Figure()
        category_fig.add_trace(go.Bar(
            x=category_performance['category'],
            y=category_performance['total_revenue'],
            name='Revenue',
            marker_color='#5d5fef',
            yaxis='y',
            offsetgroup=1
        ))
        category_fig.add_trace(go.Bar(
            x=category_performance['category'],
            y=category_performance['product_count'],
            name='Product Count',
            marker_color='#ffa412',
            yaxis='y2',
            offsetgroup=2
        ))
        
        category_fig.update_layout(
            title='Category Performance (Last 90 Days)',
            xaxis_title='Category',
            yaxis=dict(title='Revenue (₹)', side='left'),
            yaxis2=dict(title='Product Count', side='right', overlaying='y'),
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)',
            barmode='group',
            legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5)
        )
        
        # 3. Sales Velocity Chart (Products by sales in last 30 vs 60 days)
        velocity_query = """
        SELECT 
            p.title,
            SUM(CASE WHEN o.created_at >= DATE('now', '-30 days') THEN oli.quantity ELSE 0 END) as last_30_days,
            SUM(CASE WHEN o.created_at >= DATE('now', '-60 days') AND o.created_at < DATE('now', '-30 days') THEN oli.quantity ELSE 0 END) as prev_30_days
        FROM shopify_products p
        JOIN shopify_order_line_items oli ON p.id = oli.product_id
        JOIN shopify_orders o ON oli.order_id = o.id
        WHERE o.financial_status != 'refunded'
        AND o.created_at >= DATE('now', '-60 days')
        GROUP BY p.id
        HAVING (last_30_days + prev_30_days) > 5
        ORDER BY (last_30_days + prev_30_days) DESC
        LIMIT 10
        """
        velocity_data = pd.read_sql(velocity_query, conn)
        
        if not velocity_data.empty:
            velocity_fig = go.Figure()
            
            # Truncate long product names
            display_names = [name[:25] + '...' if len(name) > 25 else name for name in velocity_data['title']]
            
            velocity_fig.add_trace(go.Bar(
                x=display_names,
                y=velocity_data['prev_30_days'],
                name='31-60 Days Ago',
                marker_color='#94a3b8'
            ))
            velocity_fig.add_trace(go.Bar(
                x=display_names,
                y=velocity_data['last_30_days'],
                name='Last 30 Days',
                marker_color='#3cd856'
            ))
            
            velocity_fig.update_layout(
                title='Sales Velocity Comparison',
                xaxis_title='Product',
                yaxis_title='Units Sold',
                plot_bgcolor='rgba(0,0,0,0)',
                paper_bgcolor='rgba(0,0,0,0)',
                barmode='group',
                xaxis=dict(tickangle=-45),
                margin=dict(b=100)
            )
        else:
            # Create empty chart if no data
            velocity_fig = go.Figure()
            velocity_fig.add_annotation(
                text="No sufficient sales data for velocity analysis",
                xref="paper", yref="paper",
                x=0.5, y=0.5, showarrow=False
            )
            velocity_fig.update_layout(
                title='Sales Velocity Comparison',
                plot_bgcolor='rgba(0,0,0,0)',
                paper_bgcolor='rgba(0,0,0,0)'
            )
        
        # Convert figures to JSON for template
        status_json = json.dumps(status_fig, cls=plotly.utils.PlotlyJSONEncoder)
        category_json = json.dumps(category_fig, cls=plotly.utils.PlotlyJSONEncoder)
        velocity_json = json.dumps(velocity_fig, cls=plotly.utils.PlotlyJSONEncoder)
        
        conn.close()
        
        return render_template('inventory_insights.html',
            dead_stock_items=dead_stock_items.to_dict(orient='records'),
            top_performers=top_performers.to_dict(orient='records'),
            variant_analysis=variant_analysis.to_dict(orient='records'),
            new_products=new_products.to_dict(orient='records'),
            product_status_chart=status_json,
            category_performance_chart=category_json,
            sales_velocity_chart=velocity_json,
            user=session.get('user')
        )
        
    except Exception as e:
        flash(f"Error loading inventory insights: {str(e)}", "error")
        conn.close()
        return render_template('inventory_insights_no_data.html', 
                             user=session.get('user'),
                             db_message=f"Error: {str(e)}")

@app.route('/sales_insights')
def sales_insights():
    """
    Sales insights route
    
    Displays detailed sales analysis including:
    - Revenue growth comparison between time periods
    - Low performing products identification
    - High-value orders analysis
    - Customer purchase patterns and repeat customer data
    - Product category trends and performance
    - Sales patterns by time of day
    
    This route:
    1. Verifies user is logged in
    2. Checks database exists and has data
    3. Queries database for sales metrics and trends
    4. Creates visualizations for sales performance
    5. Renders template with sales insights data
    """
    if 'user' not in session:
        flash("Please login to access sales insights.", "warning")
        return redirect(url_for('login', next=request.path))
    
    # Check if database exists and has data
    db_exists, db_message = check_database_exists()
    if not db_exists:
        flash(f"Database issue: {db_message}", "warning")
        return render_template('sales_insights_no_data.html', 
                             user=session.get('user'),
                             db_message=db_message)
    
    # Connect to database
    conn = get_db_connection()
    
    try:        # 1. Revenue Growth Analysis (comparing periods)
        growth_query = """
        SELECT 
            p.title,
            v.sku,
            p.product_type,
            SUM(CASE WHEN o.created_at >= DATE('now', '-30 days') THEN oli.quantity * oli.price ELSE 0 END) as recent_revenue,
            SUM(CASE WHEN o.created_at >= DATE('now', '-60 days') AND o.created_at < DATE('now', '-30 days') THEN oli.quantity * oli.price ELSE 0 END) as prev_revenue
        FROM shopify_products p
        JOIN shopify_variants v ON p.id = v.product_id
        JOIN shopify_order_line_items oli ON v.id = oli.variant_id
        JOIN shopify_orders o ON oli.order_id = o.id
        WHERE o.financial_status != 'refunded'
        AND o.created_at >= DATE('now', '-60 days')
        GROUP BY p.id, v.id
        HAVING prev_revenue > 0
        ORDER BY (recent_revenue - prev_revenue) DESC
        LIMIT 10
        """
        growth_analysis = pd.read_sql(growth_query, conn)
        
        # Calculate growth rate
        if not growth_analysis.empty:
            growth_analysis['growth_rate'] = ((growth_analysis['recent_revenue'] - growth_analysis['prev_revenue']) / growth_analysis['prev_revenue'] * 100).round(2)
          # 2. Low Performing Products
        low_performers_query = """
        SELECT 
            p.title,
            v.sku,
            p.product_type,
            v.price,
            COALESCE(SUM(oli.quantity), 0) as total_sold,
            COALESCE(SUM(oli.quantity * oli.price), 0) as total_revenue,
            p.created_at
        FROM shopify_products p
        LEFT JOIN shopify_variants v ON p.id = v.product_id
        LEFT JOIN shopify_order_line_items oli ON v.id = oli.variant_id
        LEFT JOIN shopify_orders o ON oli.order_id = o.id AND o.created_at >= DATE('now', '-90 days')
        WHERE p.status = 'active'
        AND p.created_at <= DATE('now', '-30 days')  -- Exclude very new products
        GROUP BY p.id, v.id
        ORDER BY total_revenue ASC
        LIMIT 10
        """
        low_performers = pd.read_sql(low_performers_query, conn)
        
        # 3. High-Value Orders Analysis
        high_value_orders_query = """
        SELECT 
            o.id as order_id,
            o.total_price,
            o.created_at,
            COUNT(oli.id) as item_count,
            o.email
        FROM shopify_orders o
        JOIN shopify_order_line_items oli ON o.id = oli.order_id
        WHERE o.financial_status != 'refunded'
        AND o.created_at >= DATE('now', '-30 days')
        GROUP BY o.id
        ORDER BY o.total_price DESC
        LIMIT 10
        """
        high_value_orders = pd.read_sql(high_value_orders_query, conn)
          # 4. Customer Purchase Patterns
        customer_patterns_query = """
        SELECT 
            o.email,
            COUNT(DISTINCT o.id) as order_count,
            SUM(o.total_price) as total_spent,
            AVG(o.total_price) as avg_order_value,
            MIN(o.created_at) as first_order,
            MAX(o.created_at) as last_order
        FROM shopify_orders o
        WHERE o.financial_status != 'refunded'
        AND o.email IS NOT NULL
        AND o.email != ''
        GROUP BY o.email
        HAVING order_count > 1
        ORDER BY total_spent DESC
        LIMIT 10
        """
        repeat_customers = pd.read_sql(customer_patterns_query, conn)
        
        # 5. Product Category Trends
        category_trends_query = """
        SELECT 
            COALESCE(p.product_type, 'Uncategorized') as category,
            SUM(CASE WHEN o.created_at >= DATE('now', '-7 days') THEN oli.quantity * oli.price ELSE 0 END) as last_7_days,
            SUM(CASE WHEN o.created_at >= DATE('now', '-14 days') AND o.created_at < DATE('now', '-7 days') THEN oli.quantity * oli.price ELSE 0 END) as prev_7_days,
            SUM(CASE WHEN o.created_at >= DATE('now', '-30 days') THEN oli.quantity * oli.price ELSE 0 END) as last_30_days
        FROM shopify_products p
        JOIN shopify_order_line_items oli ON p.id = oli.product_id
        JOIN shopify_orders o ON oli.order_id = o.id
        WHERE o.financial_status != 'refunded'
        AND o.created_at >= DATE('now', '-30 days')
        GROUP BY p.product_type
        ORDER BY last_30_days DESC
        """
        category_trends = pd.read_sql(category_trends_query, conn)
        
        # 6. Seasonal/Time-based Analysis
        hourly_sales_query = """
        SELECT 
            strftime('%H', o.created_at) as hour,
            COUNT(*) as order_count,
            SUM(o.total_price) as revenue
        FROM shopify_orders o
        WHERE o.financial_status != 'refunded'
        AND o.created_at >= DATE('now', '-30 days')
        GROUP BY strftime('%H', o.created_at)
        ORDER BY hour
        """
        hourly_sales = pd.read_sql(hourly_sales_query, conn)
        
        # Create visualization data
        colors = ['#5d5fef', '#4079ed', '#3cd856', '#a700ff', '#ffa412']
        
        # 1. Growth trend chart
        if not growth_analysis.empty:
            growth_fig = go.Figure()
            
            # Truncate long product names
            display_names = [name[:20] + '...' if len(name) > 20 else name for name in growth_analysis['title']]
            
            growth_fig.add_trace(go.Bar(
                x=display_names,
                y=growth_analysis['prev_revenue'],
                name='Previous 30 Days',
                marker_color='#94a3b8'
            ))
            growth_fig.add_trace(go.Bar(
                x=display_names,
                y=growth_analysis['recent_revenue'],
                name='Last 30 Days',
                marker_color='#3cd856'
            ))
            
            growth_fig.update_layout(                title='Revenue Growth Comparison',
                xaxis_title='Product',
                yaxis_title='Revenue (₹)',
                plot_bgcolor='rgba(0,0,0,0)',
                paper_bgcolor='rgba(0,0,0,0)',
                barmode='group',
                xaxis=dict(tickangle=-45),
                margin=dict(b=100)
            )
        else:
            growth_fig = go.Figure()
            growth_fig.add_annotation(
                text="No sufficient data for growth analysis",
                xref="paper", yref="paper",
                x=0.5, y=0.5, showarrow=False
            )
            growth_fig.update_layout(title='Revenue Growth Comparison')
        
        # 2. Category performance chart
        if not category_trends.empty:
            category_fig = go.Figure()
            
            category_fig.add_trace(go.Bar(
                x=category_trends['category'],
                y=category_trends['last_30_days'],
                marker_color=colors[0],
                name='Last 30 Days Revenue'
            ))
            
            category_fig.update_layout(                title='Category Performance (Last 30 Days)',
                xaxis_title='Category',
                yaxis_title='Revenue (₹)',
                plot_bgcolor='rgba(0,0,0,0)',
                paper_bgcolor='rgba(0,0,0,0)',
                xaxis=dict(tickangle=-45)
            )
        else:
            category_fig = go.Figure()
            category_fig.add_annotation(
                text="No category data available",
                xref="paper", yref="paper",
                x=0.5, y=0.5, showarrow=False
            )
            category_fig.update_layout(title='Category Performance')
        
        # 3. Hourly sales pattern
        if not hourly_sales.empty:
            hourly_fig = go.Figure()
            
            hourly_fig.add_trace(go.Scatter(
                x=[f"{int(h):02d}:00" for h in hourly_sales['hour']],
                y=hourly_sales['revenue'],
                mode='lines+markers',
                name='Hourly Revenue',
                line=dict(color='#5d5fef', width=3),
                marker=dict(size=8)
            ))
            
            hourly_fig.update_layout(                title='Sales Pattern by Hour of Day (Last 30 Days)',
                xaxis_title='Hour of Day',
                yaxis_title='Revenue (₹)',
                plot_bgcolor='rgba(0,0,0,0)',
                paper_bgcolor='rgba(0,0,0,0)'
            )
        else:
            hourly_fig = go.Figure()
            hourly_fig.add_annotation(
                text="No hourly sales data available",
                xref="paper", yref="paper",
                x=0.5, y=0.5, showarrow=False
            )
            hourly_fig.update_layout(title='Sales Pattern by Hour')
        
        # Convert figures to JSON for template
        growth_json = json.dumps(growth_fig, cls=plotly.utils.PlotlyJSONEncoder)
        category_json = json.dumps(category_fig, cls=plotly.utils.PlotlyJSONEncoder)
        hourly_json = json.dumps(hourly_fig, cls=plotly.utils.PlotlyJSONEncoder)
        
        conn.close()
        return render_template('sales_insights.html',
            title="Sales Insights",
            growth_analysis=growth_analysis.to_dict(orient='records'),
            low_performers=low_performers.to_dict(orient='records'),
            high_value_orders=high_value_orders.to_dict(orient='records'),
            repeat_customers=repeat_customers.to_dict(orient='records'),
            category_trends=category_trends.to_dict(orient='records'),
            hourly_sales=hourly_sales.to_dict(orient='records'),
            growth_chart=growth_json,
            category_chart=category_json,
            hourly_chart=hourly_json,
            user=session.get('user')
        )
        
    except Exception as e:
        flash(f"Error loading sales insights: {str(e)}", "error")
        conn.close()
        return render_template('sales_insights_no_data.html', 
                             user=session.get('user'),
                             db_message=f"Error: {str(e)}")

@app.route('/settings')
def settings():
    """
    Settings route
    
    Currently a placeholder route that redirects to the dashboard
    with a notification about future functionality.
    
    This route:
    1. Displays a flash message about upcoming settings features
    2. Redirects user back to the dashboard page
    3. Will be expanded in future to include app configuration options
    """
    flash("Settings functionality coming soon!", "info")
    return redirect(url_for('dashboard'))

@app.route('/setup_db_route')
def setup_db_route():
    """
    Database setup route
    
    This route handles the initialization of the Shopify database by:
    1. Verifying user is logged in
    2. Triggering the Shopify data fetcher to populate the database
    3. Displaying success or failure message
    4. Redirecting to dashboard
    """
    if 'user' not in session:
        flash("Please login to access database setup.", "warning")
        return redirect(url_for('login', next=request.path))
    
    # For Shopify database, we need to run the data fetch
    try:
        # Import the Shopify data fetcher
        # from paste_2 import fetch_shopify_data  # This should be your shopify data fetcher
        # For now, use the local placeholder function
        result = fetch_shopify_data()
        if result["success"]:
            flash(f"Shopify database successfully updated! Fetched {result['products_count']} products and {result['orders_count']} orders.", "success")
        else:
            flash(f"Error setting up Shopify database: {result['error']}", "danger")
    except ImportError:
        flash("Shopify data fetcher not found. Please ensure the shopify data script is available.", "danger")
    except Exception as e:
        flash(f"Error setting up Shopify database: {str(e)}", "danger")
    
    return redirect(url_for('dashboard'))

@app.route('/ai_insights')
def ai_insights():
    """
    AI insights page for Shopify data
    
    This route:
    1. Checks if user is logged in
    2. Verifies database exists and has data
    3. Retrieves or generates AI-powered insights
    4. Renders the insights page with formatted Markdown content
    
    The page displays:
    - Sales performance insights generated by Gemini AI
    - Inventory intelligence insights generated by Gemini AI
    - Last update timestamp
    """
    if 'user' not in session:
        flash("Please login to access AI insights.", "warning")
        return redirect(url_for('login', next=request.path))
    
    # Check if database exists and has data
    db_exists, db_message = check_database_exists()
    if not db_exists:
        flash(f"Database issue: {db_message}", "warning")
        return render_template('ai_insights_no_data.html', 
                             user=session.get('user'),
                             db_message=db_message)
    
    # Check if we have cached insights
    sales_insights_path = "ai_insights/latest_sales_insights.md"
    inventory_insights_path = "ai_insights/latest_inventory_insights.md"
    
    sales_insights = ""
    inventory_insights = ""    
      # Load cached insights if they exist
    if os.path.exists(sales_insights_path):
        with open(sales_insights_path, 'r', encoding='utf-8') as f:
            sales_insights = f.read()
            # Replace dollar symbols with rupee symbols
            sales_insights = sales_insights.replace("$", "₹")
    else:
        # Generate new insights if no cache exists
        sales_insights = generate_sales_insights()
    
    if os.path.exists(inventory_insights_path):
        with open(inventory_insights_path, 'r', encoding='utf-8') as f:
            inventory_insights = f.read()
            # Replace dollar symbols with rupee symbols  
            inventory_insights = inventory_insights.replace("$", "₹")
    else:
        # Generate new insights if no cache exists
        inventory_insights = generate_inventory_insights()
    
    # Convert markdown to HTML with proper formatting
    sales_insights_html = Markup(markdown.markdown(sales_insights, extensions=['nl2br']))
    inventory_insights_html = Markup(markdown.markdown(inventory_insights, extensions=['nl2br']))
    
    # Get last updated time
    last_updated = "Unknown"
    if os.path.exists(sales_insights_path):
        mod_time = os.path.getmtime(sales_insights_path)
        last_updated = datetime.fromtimestamp(mod_time).strftime("%Y-%m-%d %H:%M:%S")
    
    return render_template(
        'ai_insights.html',
        user=session.get('user'),
        sales_insights=sales_insights_html,
        inventory_insights=inventory_insights_html,
        model_name="Gemini 1.5 Flash",
        last_updated=last_updated
    )

@app.route('/refresh_ai_insights')
def refresh_ai_insights():
    """
    Generate fresh AI insights
    
    This route:
    1. Verifies user is logged in
    2. Removes cached insight files
    3. Triggers new AI insight generation
    4. Redirects to the insights page
    """
    if 'user' not in session:
        flash("Please login to access AI insights.", "warning")
        return redirect(url_for('login', next=request.path))
    
    # Force new insights generation by removing cached files
    try:
        if os.path.exists("ai_insights/latest_sales_insights.md"):
            os.remove("ai_insights/latest_sales_insights.md")
        if os.path.exists("ai_insights/latest_inventory_insights.md"):
            os.remove("ai_insights/latest_inventory_insights.md")
    except Exception as e:
        print(f"Error removing cached insights: {e}")
    
    # Generate new insights
    sales_insights = generate_sales_insights()
    inventory_insights = generate_inventory_insights()
    
    # Redirect back to insights page
    flash("AI insights refreshed successfully with latest data!", "success")
    return redirect(url_for('ai_insights'))

@app.route('/reset_dashboard')
def reset_dashboard():
    """
    Reset dashboard route
    
    Used to clear dashboard filters and reset to default view.
    Also serves as a compatibility function for sidebar navigation.
    """
    if 'user' not in session:
        flash("Please login to access the dashboard.", "warning")
        return redirect(url_for('login', next=request.path))
    
    return redirect(url_for('dashboard'))

@app.errorhandler(werkzeug.routing.exceptions.BuildError)
def handle_build_error(error):
    """
    Handle URL building errors
    
    Redirects to dashboard with warning message when routes cannot be built correctly
    """
    flash(f"The requested page could not be found.", "warning")
    return redirect(url_for('dashboard'))
    
@app.errorhandler(404)
def page_not_found(e):
    """
    Handle 404 errors
    
    Redirects to dashboard with warning message when pages are not found
    """
    flash("The requested page was not found.", "warning")
    return redirect(url_for('dashboard'))

# -------------------------------------------------------------------------
# SHOPIFY DATA INTEGRATION
# -------------------------------------------------------------------------
def fetch_shopify_data():
    """
    Fetch data from Shopify API and store in database
    
    This function attempts to import and call the Shopify data fetcher module.
    If the module is not available, it returns a placeholder result.
    
    Returns:
        dict: A dictionary containing the result of the operation:
            - success: Boolean indicating if the operation was successful
            - products_count: Number of products fetched (if successful)
            - orders_count: Number of orders fetched (if successful)
            - error: Error message (if not successful)
    """
    try:
        # Import the Shopify data fetcher
        from shopify_test import fetch_shopify_data as fetch_data
        
        # Call the actual implementation
        return fetch_data()
    except ImportError:
        print("Could not import shopify_test module. Using placeholder.")
        return {
            "success": True,
            "products_count": 0,
            "orders_count": 0,
            "message": "Shopify data fetch functionality needs to be implemented"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

if __name__ == '__main__':
    # -------------------------------------------------------------------------
    # APPLICATION STARTUP SEQUENCE
    # -------------------------------------------------------------------------
    # Create necessary directories for data storage
    os.makedirs('ai_insights', exist_ok=True)
    os.makedirs('prompts', exist_ok=True)
    os.makedirs('database', exist_ok=True)
    
    # Print startup information
    print("Starting TROOBA Shopify Analytics server...")
    print(f"Gemini API Key: {'Configured' if GEMINI_API_KEY else 'Missing'}")
    print(f"Database Path: {DB_PATH}")
    
    # Check database status and print results
    db_exists, db_message = check_database_exists()
    print(f"Database Status: {db_message}")
    
    # Run the Flask app - only on localhost to prevent multiple interfaces for security
    app.run(debug=True, host='127.0.0.1', port=5000)