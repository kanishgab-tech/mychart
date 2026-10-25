import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import sqlite3
from datetime import datetime
import requests
import json
import base64
import psycopg2
from psycopg2.extras import execute_values


# Set up page configurations
st.set_page_config(layout="wide", page_title="Performance Journal and Analytics", page_icon="📊")

# ----------------------------------------------------------------------------------
# 0. SECURE USER AUTHENTICATION LAYER
# ----------------------------------------------------------------------------------
def check_authentication():
    """Validates login credentials against Streamlit production environment secrets."""
    # Ensure credentials exist in secrets configuration before validating
    if "auth" not in st.secrets:
        st.error("🔒 Security Error: Authentication secrets are missing in the server dashboard.")
        st.stop()
        
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if not st.session_state.authenticated:
        st.subheader("🔐 Access Restricted: Securities Trade Ledger")
        st.markdown("Please log in below with your administrator credentials to unlock your financial journal.")
        
        login_form = st.form("security_login_form")
        input_user = login_form.text_input("Username")
        input_pass = login_form.text_input("Password", type="password")
        submit_btn = login_form.form_submit_button("Unlock Dashboard")
        
        if submit_btn:
            if input_user == st.secrets["auth"]["username"] and input_pass == st.secrets["auth"]["password"]:
                st.session_state.authenticated = True
                st.success("Authentication successful! Loading ledger analytics...")
                st.rerun()
            else:
                st.error("Invalid username or password. Please try again.")
        st.stop() # Halts script execution for unauthenticated connections

check_authentication()

# Add a logout toggle directly to the sidebar area for clean workspace exits
if st.sidebar.button("🔒 Securely Log Out"):
    st.session_state.authenticated = False
    st.rerun()


st.title("📊 Trade Ledger & Analytics Dashboard")
DB_FILE = "trade_ledger.db"

# ----------------------------------------------------------------------------------
# 1. DATABASE INITIALIZATION & ENGINE LAYER (CLEAN INLINE SCHEMA DEFINITIONS)
# ----------------------------------------------------------------------------------
def init_database():
    """Initializes SQLite database tables natively with empty tracking columns."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 1. Realized Ledger (For Completed Trades)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS realized_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_symbol TEXT,
            buy_qty REAL,
            buy_value REAL,
            buy_date TEXT,
            sell_qty REAL,
            sell_value REAL,
            sell_date TEXT,
            stt REAL,
            profit_loss REAL,
            dividend REAL,
            total_income REAL,
            hold_days INTEGER,
            sector TEXT,
            market_cap_tier TEXT,
            is_etf INTEGER,
            UNIQUE(stock_symbol, buy_qty, buy_date, sell_date)
        )
    """)
    
    # 2. Portfolio Snapshots (Natively includes strategy and reference fields as clean defaults)
        # Update the snapshot table creation inside Section 1 to register the manual column
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT,
            stock_name TEXT,
            isin TEXT,
            sector_name TEXT,
            quantity REAL,
            average_cost REAL,
            value_at_cost REAL,
            current_market_price REAL,
            unrealized_pnl REAL,
            realized_pnl REAL,
            market_cap_tier TEXT,
            is_etf INTEGER,
            strategy TEXT DEFAULT 'Select Strategy',
            notes_reference TEXT DEFAULT '',
            buy_date_manual TEXT,
            UNIQUE(snapshot_date, stock_name)
        )
    """)

    conn.commit()
    conn.close()

init_database()

# ----------------------------------------------------------------------------------
# 3. CLOUD NATIVE SUPABASE POSTGRES DB INTEGRATION ENGINE
# ----------------------------------------------------------------------------------
def get_supabase_connection():
    """Establishes an encrypted network connection block to your cloud database."""
    if "supabase" not in st.secrets:
        st.error("🔒 Configuration Error: Supabase strings are missing in your server dash.")
        return None
    try:
        conn = psycopg2.connect(st.secrets["supabase"]["connection_string"])
        return conn
    except Exception as e:
        st.sidebar.error(f"Cloud DB Connection Failed: {str(e)}")
        return None

def save_metadata_to_supabase(stock_name, strategy, notes, buy_date_manual):
    """Saves or updates row annotations inside the permanent cloud database tables."""
    conn = get_supabase_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        # Execute an UPSERT operation to update records gracefully on primary key conflicts
        cursor.execute("""
            INSERT INTO portfolio_notes_store (stock_name, strategy, notes_reference, buy_date_manual, updated_at)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (stock_name) 
            DO UPDATE SET 
                strategy = EXCLUDED.strategy,
                notes_reference = EXCLUDED.notes_reference,
                buy_date_manual = EXCLUDED.buy_date_manual,
                updated_at = CURRENT_TIMESTAMP;
        """, (str(stock_name).strip(), str(strategy), str(notes), str(buy_date_manual)))
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        st.sidebar.error(f"Cloud write crash: {str(e)}")
        return False

def load_all_metadata_from_supabase():
    """Loads all saved strategies and manual note parameters from cloud memory on startup."""
    meta_strat_map, meta_notes_map, meta_date_map = {}, {}, {}
    conn = get_supabase_connection()
    if not conn:
        return meta_strat_map, meta_notes_map, meta_date_map
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT stock_name, strategy, notes_reference, buy_date_manual FROM portfolio_notes_store;")
        rows = cursor.fetchall()
        for row in rows:
            s_name = row[0]
            meta_strat_map[s_name] = row[1]
            meta_notes_map[s_name] = row[2]
            meta_date_map[s_name] = row[3]
        cursor.close()
        conn.close()
    except Exception:
        pass
    return meta_strat_map, meta_notes_map, meta_date_map



# ----------------------------------------------------------------------------------
# 2. STRATEGIC METADATA CLASSIFIERS
# ----------------------------------------------------------------------------------
def check_if_etf(stock_name):
    """Flags if an asset is an ETF (returns 1 for True, 0 for False)."""
    name = str(stock_name).upper()
    etf_keywords = ['ETF', 'BEES', 'GOLD', 'SILVER']
    return 1 if any(keyword in name for keyword in etf_keywords) else 0

def classify_market_cap(stock_name):
    """Rule-based lookup for market cap sorting matching NSE definitions."""
    name = str(stock_name).upper()
    if check_if_etf(name) == 1:
        return 'ETF / Gold'
        
    large_caps = ['STATE BANK OF INDIA', 'ADANI GREEN ENERGY LTD', 'BHARTI AIRTEL LTD', 'TVS MOTOR COMPANY LTD']
    mid_caps = ['BANK OF BARODA', 'BIOCON LTD', 'GAIL (INDIA) LTD', 'NTPC LTD', 'SAIL', 'STEEL AUTHORITY OF INDIA LTD']
    
    if any(lc in name for lc in large_caps):
        return 'Large Cap'
    elif any(mc in name for mc in mid_caps):
        return 'Mid Cap'
    else:
        return 'Small Cap'

def excel_date_to_string(serial):
    """Converts Excel sequential serial numbers to YYYY-MM-DD text for SQLite."""
    try:
        s = float(serial)
        if s > 0:
            dt = pd.to_datetime('1899-12-30') + pd.to_timedelta(s, unit='D')
            return dt.strftime('%Y-%m-%d')
    except:
        return None
    return None

# ----------------------------------------------------------------------------------
# 3. DATABASE SYNCHRONIZATION PIPELINES
# ----------------------------------------------------------------------------------
def sync_closed_trades_to_sql(df):
    """Saves rows into storage ledger seamlessly without manual entry duplication."""
    if df.empty:
        return 0
    conn = sqlite3.connect(DB_FILE)
    rows_inserted = 0
    for _, row in df.iterrows():
        try:
            conn.execute("""
                INSERT OR IGNORE INTO realized_ledger 
                (stock_symbol, buy_qty, buy_value, buy_date, sell_qty, sell_value, sell_date, stt, profit_loss, dividend, total_income, hold_days, sector, market_cap_tier, is_etf)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                row['STOCK SYMBOL'], row['BUY TRANSACTION QTY'], row['BUY TRANSACTION VALUE'],
                str(row['BUY_DATE_CLEAN']), row['SELL TRANSACTION QTY'], row['SELL TRANSACTION VALUE'],
                str(row['SELL_DATE_CLEAN']), row['STT'], row['PROFIT/LOSS'], row['DIVIDEND/INTEREST'],
                row['TOTAL INCOME'], int(row['HOLD_DAYS']) if pd.notna(row['HOLD_DAYS']) else None,
                row['Sector'], row['Market Cap Tier'], int(row['IS_ETF'])
            ))
            rows_inserted += conn.total_changes
        except Exception as e:
            pass
    conn.commit()
    conn.close()
    return rows_inserted

def sync_open_snapshot_to_sql(df):
    """Logs the active daily portfolio metrics to run time-series comparisons."""
    if df.empty:
        return 0
    today_str = datetime.today().strftime('%Y-%m-%d')
    conn = sqlite3.connect(DB_FILE)
    rows_inserted = 0
    for _, row in df.iterrows():
        try:
            conn.execute("""
                INSERT OR REPLACE INTO portfolio_snapshots 
                (snapshot_date, stock_name, isin, sector_name, quantity, average_cost, value_at_cost, current_market_price, unrealized_pnl, realized_pnl, market_cap_tier, is_etf)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                today_str, row['Stock Name'], row['ISIN'], row['Sector Name'],
                row['Quantity'], row['Average Cost Price'], row['Value At Cost'],
                row['Current Market Price'], row['Unrealized Profit/Loss'], row['Realized Profit/Loss'],
                row['Market Cap Tier'], int(row['IS_ETF'])
            ))
            rows_inserted += conn.total_changes
        except Exception as e:
            pass
    conn.commit()
    conn.close()
    return rows_inserted

# ----------------------------------------------------------------------------------
# 4. SIDEBAR UPLOAD MANAGEMENT (FIXED DATE PARSING & CORRECTED LEDGER SYNC)
# ----------------------------------------------------------------------------------
st.sidebar.header("📁 Ingest Broker Daily Sheets")
closed_file = st.sidebar.file_uploader("Upload Profit & Loss Details (.xls/.xlsx)", type=["xls", "xlsx", "csv"])
open_file = st.sidebar.file_uploader("Upload Equity Summary Details (.xls/.xlsx)", type=["xls", "xlsx", "csv"])

# Define structural helper function inside ingestion block to resolve serial type drift
def clean_and_format_date_string(date_val):
    """Converts mixed type excel inputs into clean standardized YYYY-MM-DD strings."""
    if pd.isna(date_val) or str(date_val).strip() == "":
        return None
    try:
        # Check if the entry is an Excel sequential serial integer float
        val_float = float(date_val)
        dt = pd.to_datetime('1899-12-30') + pd.to_timedelta(val_float, unit='D')
        return dt.strftime('%Y-%m-%d')
    except (ValueError, TypeError):
        # Fallback parsing strategy if string already resembles standard date formats
        try:
            dt = pd.to_datetime(str(date_val).strip())
            return dt.strftime('%Y-%m-%d')
        except:
            return None

if closed_file is not None:
    try:
        df_c = pd.read_excel(closed_file)
    except Exception:
        df_c = pd.read_csv(closed_file)
    
    # Standardize headers to fix key visibility bugs
    df_c.columns = df_c.columns.str.strip()
    df_c = df_c[df_c['STOCK SYMBOL'].notna() & (df_c['BUY TRANSACTION QTY'] > 0)]
    
    # 1. PARSE SERIAL TIMESTAMPS AND MAP INTO UNIFORM CHARACTER ARRAYS
    df_c['BUY_DATE_CLEAN'] = df_c['BUY TRANSACTION DATE'].apply(clean_and_format_date_string)
    df_c['SELL_DATE_CLEAN'] = df_c['SELL TRANSACTION DATE'].apply(clean_and_format_date_string)
    
    # 2. RUN NUMERIC CONVERSIONS PRIOR TO PERFORMING MATRIC MATH
    b_date_dt = pd.to_datetime(df_c['BUY_DATE_CLEAN'], errors='coerce')
    s_date_dt = pd.to_datetime(df_c['SELL_DATE_CLEAN'], errors='coerce')
    
    # Calculate exact delta duration windows 
    df_c['HOLD_DAYS'] = (s_date_dt - b_date_dt).dt.days
    df_c['IS_ETF'] = df_c['STOCK SYMBOL'].apply(check_if_etf)
    df_c['Market Cap Tier'] = df_c['STOCK SYMBOL'].apply(classify_market_cap)
    df_c['Sector'] = 'Others' # Base sector string initialized before runtime joins
    
    # 3. INTERCEPT BASELINE SYNCHRONIZATION ARRAYS TO PREVENT DATA DRIFT
    conn_sync = sqlite3.connect(DB_FILE)
    for _, row in df_c.iterrows():
        try:
            conn_sync.execute("""
                INSERT OR IGNORE INTO realized_ledger 
                (stock_symbol, buy_qty, buy_value, buy_date, sell_qty, sell_value, sell_date, stt, profit_loss, dividend, total_income, hold_days, sector, market_cap_tier, is_etf)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                row['STOCK SYMBOL'], float(row['BUY TRANSACTION QTY']), float(row['BUY TRANSACTION VALUE']),
                row['BUY_DATE_CLEAN'], float(row['SELL TRANSACTION QTY']), float(row['SELL TRANSACTION VALUE']),
                row['SELL_DATE_CLEAN'], float(row['STT']), float(row['PROFIT/LOSS']), float(row['DIVIDEND/INTEREST']),
                float(row['TOTAL INCOME']), int(row['HOLD_DAYS']) if pd.notna(row['HOLD_DAYS']) else None,
                row['Sector'], row['Market Cap Tier'], int(row['IS_ETF'])
            ))
        except Exception as e:
            pass
    conn_sync.commit()
    conn_sync.close()
    st.sidebar.success("Closed transactions successfully cleaned, calculated and committed to SQLite storage.")

if open_file is not None:
    try:
        df_o = pd.read_excel(open_file, skiprows=4)
    except Exception:
        df_o = pd.read_csv(open_file, skiprows=4)
        
    df_o.columns = df_o.columns.str.strip()
    df_o = df_o[df_o['Stock Name'].notna() & (df_o['Stock Name'] != 'TOTAL')]
    
    # Convert numerical column values to float objects safely
    for col in ['Valuation at Current Market Price', 'Value At Cost', 'Unrealized Profit/Loss', 'Current Market Price', 'Average Cost Price', 'Quantity', 'Realized Profit/Loss']:
        if col in df_o.columns:
            df_o[col] = pd.to_numeric(df_o[col], errors='coerce').fillna(0.0)
            
    df_o['IS_ETF'] = df_o['Stock Name'].apply(check_if_etf)
    df_o['Market Cap Tier'] = df_o['Stock Name'].apply(classify_market_cap)
    
    sync_open_snapshot_to_sql(df_o)
    st.sidebar.success("Daily active holding snapshots securely written to SQLite metrics caches.")


# ----------------------------------------------------------------------------------
# 5. READ FROM DATABASE STORAGE FOR DASHBOARD RENDERING
# ----------------------------------------------------------------------------------
conn = sqlite3.connect(DB_FILE)
df_sql_closed = pd.read_sql_query("SELECT * FROM realized_ledger", conn)

# Pull the latest snapshot date logged to represent active positions
latest_date_query = "SELECT max(snapshot_date) FROM portfolio_snapshots"
cursor = conn.cursor()
cursor.execute(latest_date_query)
latest_date = cursor.fetchone()[0]

if latest_date:
    df_sql_open = pd.read_sql_query(f"SELECT * FROM portfolio_snapshots WHERE snapshot_date = '{latest_date}'", conn)
else:
    df_sql_open = pd.DataFrame()
conn.close()

# Handle column string modifications back to processing requirements
if not df_sql_closed.empty:
    # Backfill sectors from active stock definitions to match histories
    if not df_sql_open.empty:
        sector_mapping = dict(zip(df_sql_open['stock_name'].str.upper(), df_sql_open['sector_name']))
        df_sql_closed['sector'] = df_sql_closed['stock_symbol'].str.upper().map(sector_mapping).fillna('Others')

# ----------------------------------------------------------------------------------
# 5. TABBED VIEWS CONFIGURATION
# ----------------------------------------------------------------------------------
tab_main, tab_etf, tab_swing, tab_lt, tab_hist = st.tabs([
"📈 Executive Dashboard",
"🟡 Isolated ETF Trackers",
"🕒 Active Swing Book (Stocks Only)",
"🏆 Core Long-Term Book (Stocks Only)",
"🗄️ Historical Database Ledger"
])

# ----------------------------------------------------------------------------------
# MAIN SUMMARY DASHBOARD (FIXED REALIZED DATA SOURCING LOOP)
# ----------------------------------------------------------------------------------
with tab_main:
        st.subheader("📋 Key Portfolio Health Metrics - Stocks Only")
        df_open_stocks = df_sql_open[df_sql_open['is_etf'] == 0].copy()

        # 1. BASELINE VALUE AGGREGATIONS FOR TOP INDICATORS
        if not df_open_stocks.empty:
            total_cost = df_open_stocks['value_at_cost'].sum()
            current_val_fixed = df_open_stocks['value_at_cost'].sum() + df_open_stocks['unrealized_pnl'].sum()
            unrealized_pnl = df_open_stocks['unrealized_pnl'].sum()
        else:
            total_cost, current_val_fixed, unrealized_pnl = 0.0, 0.0, 0.0
        
        if not df_sql_closed.empty:
            wins = df_sql_closed[df_sql_closed['profit_loss'] > 0].shape[0]
            win_rate = (wins / df_sql_closed.shape[0]) * 100 if df_sql_closed.shape[0] > 0 else 0.0
        else:
            win_rate = 0.0

        # Render Core Top-Level KPI Summary Blocks
        m_col1, m_col2, m_col3, m_col4 = st.columns(4)
        m_col1.metric("Current Asset Valuation", f"₹ {current_val_fixed:,.2f}")
        m_col2.metric("Total Invested Cost", f"₹ {total_cost:,.2f}")
        m_col3.metric("Net Unrealized P&L", f"₹ {unrealized_pnl:,.2f}")
        m_col4.metric("Trading Accuracy (Win Rate)", f"{win_rate:.1f}%")

        st.markdown("---")
        
           # ------------------------------------------------------------------------------
        # FIXED LOGIC: DIRECT STRUCTURAL BOUNDARY SOURCING FROM CLOSED DATA TABLES
        # ------------------------------------------------------------------------------
        st.markdown("### 🗓️ Periodic Realized Performance Breakdown")
        st.markdown("Realized closing performance matrix mapped across strategy classifications.")

        # Establish anchor execution time parameters (Matching your data log timeline anchor: Oct 20, 2026)
        current_date =  datetime.today().date()  #pd.Timestamp("2026-10-20")
        current_year = current_date.year
        current_month = current_date.month
        
        # Calculate Indian Financial Year boundaries (April to March) as standard pandas Timestamps
        if current_month >= 4:
            fy_start = pd.Timestamp(year=current_year, month=4, day=1)
            fy_end = pd.Timestamp(year=current_year + 1, month=3, day=31)
        else:
            fy_start = pd.Timestamp(year=current_year - 1, month=4, day=1)
            fy_end = pd.Timestamp(year=current_year, month=3, day=31)
            
        # Calculate Calendar Month boundaries as standard pandas Timestamps
        month_start = pd.Timestamp(year=current_year, month=current_month, day=1)
        month_end = pd.Timestamp(year=current_year, month=current_month, day=31)

        # Secure core metrics out of historical closed trade data arrays directly
        if not df_sql_closed.empty:
           # st.dataframe(df_sql_closed[['stock_symbol', 'buy_date', 'sell_date', 'hold_days', 'profit_loss', 'sector']], use_container_width=True)
            df_calc = df_sql_closed.copy()
            
            # 1. CLEAN DATETIME FIELDS FOR UNIFORM SYSTEM MATCHES
            df_calc['sell_date_clean_str'] = df_calc['sell_date'].astype(str).str.strip().str[:10]
            df_calc['sell_date_dt'] = pd.to_datetime(df_calc['sell_date_clean_str'], errors='coerce')
            
            # 2. ENFORCE DATA CONVERSIONS TO FLOATING NUMBERS FOR COMPUTATIONS
            df_calc['profit_loss'] = pd.to_numeric(df_calc['profit_loss'], errors='coerce').fillna(0.0)
            df_calc['buy_value'] = pd.to_numeric(df_calc['buy_value'], errors='coerce').fillna(0.0)
            df_calc['hold_days'] = pd.to_numeric(df_calc['hold_days'], errors='coerce').fillna(0)
            df_calc['is_etf'] = pd.to_numeric(df_calc['is_etf'], errors='coerce').fillna(0)

            # 3. DIRECT STRUCTURAL ASSIGNMENTS BYPASSING SCRIPT SYNCHRONIZATION DEPENDENCIES
            df_calc['Strategy_Type'] = np.where(df_calc['is_etf'] == 1, 'ETF Tracking',
                                    np.where(df_calc['hold_days'] <= 60, 'Swing Book', 'Long Term Core'))
            
            # Apply data slices to datetime fields
            df_fy = df_calc[(df_calc['sell_date_dt'] >= fy_start) & (df_calc['sell_date_dt'] <= fy_end)]
            df_mo = df_calc[(df_calc['sell_date_dt'] >= month_start) & (df_calc['sell_date_dt'] <= month_end)]
            
            # Helper function to compute absolute P&L and ROI percentages safely from closed limits
            def calculate_row_metrics(df_subset):
                summary = {}
                for strat in ['Swing Book', 'Long Term Core', 'ETF Tracking']:
                    sub = df_subset[df_subset['Strategy_Type'] == strat]
                    total_pnl = sub['profit_loss'].sum()
                    total_cost_basis = sub['buy_value'].sum()
                    roi_pct = (total_pnl / total_cost_basis * 100) if total_cost_basis > 0 else 0.0
                    summary[strat] = (total_pnl, roi_pct)
                return summary

            fy_data = calculate_row_metrics(df_fy)
            mo_data = calculate_row_metrics(df_mo)
            
            # Construct a high-scannability matrix table representation
            matrix_rows = []
            for strategy in ['Swing Book', 'Long Term Core', 'ETF Tracking']:
                pnl_fy, roi_fy = fy_data[strategy]
                pnl_mo, roi_mo = mo_data[strategy]
                
                matrix_rows.append({
                    "Strategy Group": strategy,
                    "FY Realized P/L Amount": f"₹ {pnl_fy:,.2f}",
                    "FY Realized P/L %": f"{roi_fy:+.2f}%",
                    "Current Month P/L Amount": f"₹ {pnl_mo:,.2f}",
                    "Current Month P/L %": f"{roi_mo:+.2f}%"
                })
            
            df_matrix_ui = pd.DataFrame(matrix_rows)
            st.table(df_matrix_ui)
        else:
            st.info("No historical realized trades recorded inside database ledger file to process performance summaries.")

        
        # ------------------------------------------------------------------------------
        # UPDATED INSERT: DATE FILTER, DUAL LINE TOGGLE, MAX DRAWDOWN & TREND CURVES
        # ------------------------------------------------------------------------------
        st.subheader("📈 Performance Trend Curve(ETF & Stocks)")
        
        # Pull baseline raw dates directly from SQLite to determine data boundary limits
        conn = sqlite3.connect(DB_FILE)
        df_trend_raw = pd.read_sql_query("""
            SELECT 
                snapshot_date, 
                SUM(value_at_cost) as total_invested,
                SUM(value_at_cost + unrealized_pnl) as total_valuation,
                SUM(unrealized_pnl) as net_unrealized_pnl
            FROM portfolio_snapshots 
            GROUP BY snapshot_date
            ORDER BY snapshot_date ASC
        """, conn)
        conn.close()
        
        if not df_trend_raw.empty and len(df_trend_raw) > 0:
            # Convert raw date string indices to datetime types for comparison layers
            df_trend_raw['snapshot_date_dt'] = pd.to_datetime(df_trend_raw['snapshot_date']).dt.date
            
            # --- CALCULATE CORE MAX DRAWDOWN VALUE ---
            # Compute cumulative rolling peak of the portfolio valuation
            df_trend_raw['rolling_peak'] = df_trend_raw['total_valuation'].cummax()
            # Calculate the dropdown value from the peak
            df_trend_raw['drawdown_val'] = df_trend_raw['total_valuation'] - df_trend_raw['rolling_peak']
            # Calculate the dropdown percentage from the peak
            df_trend_raw['drawdown_pct'] = (df_trend_raw['drawdown_val'] / df_trend_raw['rolling_peak']) * 100
            
            max_drawdown_val = df_trend_raw['drawdown_val'].min()
            max_drawdown_pct = df_trend_raw['drawdown_pct'].min()
            
            # Set default fallback string defaults for single-point instances
            mdd_string = f"₹ {max_drawdown_val:,.2f} ({max_drawdown_pct:.2f}%)" if max_drawdown_val < 0 else "0.00% (No Drawdowns)"
            
            min_date = df_trend_raw['snapshot_date_dt'].min()
            max_date = df_trend_raw['snapshot_date_dt'].max()
            
            # Setup interactive layout columns for controls and metrics side-by-side
            ctrl_col1, ctrl_col2, metric_col = st.columns([2, 2, 2])
            
            with ctrl_col1:
                st.markdown("##### Filter Valuation Window Range:")
                date_range = st.date_input(
                    "Select Date Parameters Range:",
                    value=(min_date, max_date),
                    min_value=min_date,
                    max_value=max_date,
                    label_visibility="collapsed"
                )
                
            with ctrl_col2:
                st.markdown("##### Visual Overlays:")
                show_invested = st.checkbox("Show Total Invested Capital", value=True)
                
            with metric_col:
                # Specialized risk indicator container layout box
                st.metric(
                    label="📉 Historical Max Drawdown (Peak Drop)", 
                    value=mdd_string,
                    delta="Risk Metric Exposure",
                    delta_color="inverse"
                )

             # ------------------------------------------------------------------------------
            # NEW INSERT: AUTOMATED RISK NOTIFICATION THRESHOLD BANNERS
            # ------------------------------------------------------------------------------
            # Pull the single most recent drawdown point from the tracking history matrix
            current_drawdown_pct = df_trend_raw['drawdown_pct'].iloc[-1]
            current_drawdown_val = df_trend_raw['drawdown_val'].iloc[-1]
            
            if current_drawdown_pct <= -15.0:
                st.error(
                    f"🚨 **CRITICAL RISK ALERT:** Your current portfolio drawdown has reached "
                    f"**{current_drawdown_pct:.2f}%** (Loss of ₹ {abs(current_drawdown_val):,.2f} from peak). "
                    f"Consider reviewing risk limits or tightening protective stop-losses across your Swing Book."
                )
            elif current_drawdown_pct <= -5.0:
                st.warning(
                    f"⚠️ **MODERATE RISK WARNING:** Current equity drawdown is sitting at "
                    f"**{current_drawdown_pct:.2f}%** (Loss of ₹ {abs(current_drawdown_val):,.2f} from peak). "
                    f"Portfolio volatility is rising above historical baseline parameters."
                )
            else:
                st.success(
                    f"✅ **RISK PROFILE STABLE:** Current drawdown is well within acceptable safety parameters "
                    f"({current_drawdown_pct:.2f}% drop from peak values)."
                )
                
            # Safeguard parsing process to wait until user defines both date bounds
            if isinstance(date_range, tuple) and len(date_range) == 2:
                start_date, end_date = date_range
                df_trend_filtered = df_trend_raw[
                    (df_trend_raw['snapshot_date_dt'] >= start_date) & 
                    (df_trend_raw['snapshot_date_dt'] <= end_date)
                ].copy()
            else:
                df_trend_filtered = df_trend_raw.copy()
            
            # Build chart with filtered rows
            if not df_trend_filtered.empty:
                fig_trend = px.line(
                    df_trend_filtered, 
                    x="snapshot_date", 
                    y="total_valuation",
                    labels={"snapshot_date": "Date", "value": "Capital Metric Value (₹)"},
                    title="Account Value vs. Invested Capital Growth Metric Curve Over Time",
                    markers=True
                )
                
                fig_trend.update_traces(
                    name="Total Valuation",
                    line_color="#1f77b4", 
                    line_width=3,
                    hovertemplate="<b>Date:</b> %{x}<br><b>Account Value:</b> ₹%{y:,.2f}<extra></extra>"
                )
                
                if show_invested:
                    fig_trend.add_scatter(
                        x=df_trend_filtered["snapshot_date"],
                        y=df_trend_filtered["total_invested"],
                        mode="lines+markers",
                        name="Invested Cost",
                        line=dict(color="#ff7f0e", width=2, dash="dash"),
                        hovertemplate="<b>Date:</b> %{x}<br><b>Invested Cost:</b> ₹%{y:,.2f}<extra></extra>"
                    )
                
                fig_trend.update_layout(
                    hovermode="x unified",
                    xaxis_gridcolor="#eeeeee",
                    yaxis_gridcolor="#eeeeee",
                    plot_bgcolor="rgba(0,0,0,0)",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                )
                
                st.plotly_chart(fig_trend, use_container_width=True)
            else:
                st.warning("No tracking points found matching your selected date constraints.")
        else:
            st.info("Insufficient snapshot history points found to map trend lines. Upload daily summaries to capture intervals.")

        st.markdown("---")
            # [The structural layout proceeds to Asset Allocation Distributions below...]
        st.subheader("Asset Allocation Distributions(Stocks Only):")
    

        chart_col1, chart_col2 = st.columns(2)
        df_open_stocks_asset_classification = df_sql_open[df_sql_open['is_etf'] == 0].copy()
        with chart_col1:
            df_sector = df_open_stocks_asset_classification.groupby('sector_name')['value_at_cost'].sum().reset_index()
            fig_sector = px.pie(df_sector, values='value_at_cost', names='sector_name', 
                                title='Sector-wise Fund Allocation', hole=0.4,
                                color_discrete_sequence=px.colors.sequential.YlOrRd[::-1])
            st.plotly_chart(fig_sector, use_container_width=True)
            
        with chart_col2:
            df_cap = df_open_stocks_asset_classification.groupby('market_cap_tier')['value_at_cost'].sum().reset_index()
            fig_cap = px.bar(df_cap, x='market_cap_tier', y='value_at_cost',
                                title='Capital Weights by Market Tier', text_auto=',.2f',
                                labels={'value_at_cost': 'Total Capital (₹)'},
                                color='market_cap_tier', color_discrete_sequence=px.colors.qualitative.Bold)
            st.plotly_chart(fig_cap, use_container_width=True)    
        
        st.markdown("---")
        # ------------------------------------------------------------------------------
        # UPDATED INSERT: ACTIVE CAPITAL VOLUME DISTRIBUTION (OPEN STOCKS ONLY)
            # ------------------------------------------------------------------------------
        st.subheader("📊 Active Capital Volume Allocation Analysis")
        st.markdown("Track floating capital value concentrations across open stock positions.")
        
        if not df_sql_open.empty:
            # 1. Filter out index pools and ETFs to focus exclusively on active equity strategies
            df_open_stocks_only = df_sql_open[df_sql_open['is_etf'] == 0].copy()
            
            if not df_open_stocks_only.empty:
                # 2. Compute live current capital volume exposure per stock
                df_open_stocks_only['current_volume_valuation'] = (
                    df_open_stocks_only['quantity'] * df_open_stocks_only['current_market_price']
                )
                
                # 3. Aggregate and sort by active capital size
                df_vol_agg = df_open_stocks_only.groupby(['stock_name', 'sector_name'])['current_volume_valuation'].sum().reset_index()
                df_vol_agg = df_vol_agg.sort_values(by='current_volume_valuation', ascending=False)
                
                # Construct an interactive breakdown bar chart with functional hover tooltips
                fig_vol = px.bar(
                    df_vol_agg,
                    x='stock_name',
                    y='current_volume_valuation',
                    color='sector_name',
                    title='',
                    labels={
                        'stock_name': 'Stock Name Ticker',
                        'current_volume_valuation': 'Active Valuation Volume (₹)',
                        'sector_name': 'Market Sector'
                    },
                    text_auto=',.2f',
                    color_discrete_sequence=px.colors.qualitative.Dark24
                )
                
                # Apply layout styling to optimize spacing
                fig_vol.update_layout(
                    height=500,
                    xaxis_tickangle=-45,
                    hovermode="x unified",
                    xaxis_gridcolor="#eeeeee",
                    yaxis_gridcolor="#eeeeee",
                    plot_bgcolor="rgba(0,0,0,0)",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0)
                )
                
                # Inject precise live financial tooltips on block bar hover
                fig_vol.update_traces(
                    hovertemplate="<b>Ticker:</b> %{x}<br><b>Current Floating Value:</b> ₹%{y:,.2f}<extra></extra>"
                )
                
                st.plotly_chart(fig_vol, use_container_width=True)
            else:
                st.info("No active open stock positions found (excluding ETFs) to generate allocation metrics.")
        else:
            st.info("No live portfolio snapshot data cached in local database tables to generate volume analytics.")
        st.markdown("---")         
                    
        # ------------------------------------------------------------------------------
        # NEW INSERT: WIN/LOSS PERFORMANCE SCATTERPLOT MATRIX
        # ------------------------------------------------------------------------------
        st.subheader("🎯 Trading Edge: Win/Loss vs. Holding Time Scatterplot")
        st.markdown("Analyze trade distribution to verify risk-reward parameters.")
        st.markdown("**Top-Left Quadrant**= 'High Profit, Short Hold',     **Top-Right Quadrant**= 'High Profit, Long Hold'")
        st.markdown("**Bottom-Left Quadrant**= 'Small Loss, Short Hold',   **Bottom-Right Quadrant**= 'Large Loss, Long Hold'")

        
        if not df_sql_closed.empty:
            # Create clean outcome tags based on the trade's P&L
            df_sql_closed['Outcome'] = np.where(df_sql_closed['profit_loss'] > 0, 'Win', 'Loss')
            
            # Build an interactive scatterplot tracking individual trade attributes
            fig_scatter = px.scatter(
                df_sql_closed,
                x='hold_days',
                y='profit_loss',
                color='Outcome',
                size=df_sql_closed['buy_value'].abs(), # Node sizing reflects position capital size
                hover_name='stock_symbol',
                labels={
                    'hold_days': 'Holding Time Duration (Days)',
                    'profit_loss': 'Realized Profit / Loss (₹)',
                    'Outcome': 'Trade Result'
                },
                title='Trade Distribution Matrix (Holding Days vs. Profitability)',
                color_discrete_map={'Win': '#2ca02c', 'Loss': '#d62728'}, # Emerald Win Green vs Crimson Loss Red
                category_orders={'Outcome': ['Win', 'Loss']}
            )
            
            # Inject precise tracking attributes into the pop-up tooltip boxes
            fig_scatter.update_traces(
                hovertemplate="<b>Ticker:</b> %{hovertext}<br>" +
                                "<b>Holding Period:</b> %{x} Days<br>" +
                                "<b>Net Return:</b> ₹%{y:,.2f}<br><extra></extra>"
            )
            
            # Add horizontal reference marker at 0.00 to clearly separate wins from losses
            fig_scatter.add_hline(y=0, line_dash="dash", line_color="#7f7f7f", line_width=1.5)
            
            fig_scatter.update_layout(
                height=550,
                xaxis_gridcolor="#eeeeee",
                yaxis_gridcolor="#eeeeee",
                plot_bgcolor="rgba(0,0,0,0)",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            
            st.plotly_chart(fig_scatter, use_container_width=True)
        else:
            st.info("No completed historical trades available in the database ledger to generate scatter metrics.")


# --------------------------------------------------------------# ----------------------------------------------------------------------------------
# NEW TAB: ISOLATED ETF TRACKERS (Gold, Silver, Index Funds - UPDATED VERTICAL STACK LAYOUT)
# ----------------------------------------------------------------------------------
with tab_etf:
    
    # ------------------------------------------------------------------------------
    # POSITION GRID 1: ACTIVE OPEN ETF ALLOCATIONS (TOP ROW BLOCK)
    # ------------------------------------------------------------------------------
    st.markdown("### 📊 Active Open ETF Positions")
    st.markdown("Gold/Silver/ETF Funds portfolios currently held in your active storage.")
    
    if not df_sql_open.empty:
        df_open_etf = df_sql_open[df_sql_open['is_etf'] == 1].copy()
        if not df_open_etf.empty:

        # Local Summary KPI Blocks for Open Allocations
            open_etf_total_cost = df_open_etf['value_at_cost'].sum()
            open_etf_total_val = df_open_etf['value_at_cost'].sum() + df_open_etf['unrealized_pnl'].sum()
            open_etf_net_pnl = df_open_etf['unrealized_pnl'].sum()
            open_etf_net_roi = (open_etf_net_pnl / df_open_etf['value_at_cost'].sum() * 100) if df_open_etf['value_at_cost'].sum() > 0 else 0.0
            
            okpi1, okpi2, okpi3 = st.columns(3)
            okpi1.metric("Open ETF Investment Value", f"₹ {open_etf_total_cost:,.2f}")
            okpi2.metric("Open ETF Current Asset Value", f"₹ {open_etf_total_val:,.2f}")
            okpi3.metric("Floating ETF P&L Tracker", f"₹ {open_etf_net_pnl:,.2f}", f"{open_etf_net_roi:+.2f}%")
         
            
            # Calculate running floating return percentage metrics
            df_open_etf['unrealized_pnl_pct'] = np.where(
                df_open_etf['value_at_cost'] > 0,
                (df_open_etf['unrealized_pnl'] / df_open_etf['value_at_cost']) * 100,
                0.0
            )
            
            # Format active display ledger grid
            st.dataframe(
                df_open_etf[['stock_name', 'quantity', 'average_cost', 'value_at_cost', 'current_market_price', 'unrealized_pnl', 'unrealized_pnl_pct']], 
                column_config={
                    "stock_name": st.column_config.TextColumn("ETF Fund Asset Name"),
                    "quantity": st.column_config.NumberColumn("Qty"),
                    "average_cost": st.column_config.NumberColumn("Avg Cost Base", format="₹%.2f"),
                    "value_at_cost": st.column_config.NumberColumn("Value Cost", format="₹%.2f"),
                    "current_market_price": st.column_config.NumberColumn("CMP", format="₹%.2f"),
                    "unrealized_pnl": st.column_config.NumberColumn("Unrealized P&L Amount", format="₹%.2f"),
                    "unrealized_pnl_pct": st.column_config.NumberColumn("Unrealized P&L %", format="%.2f%%")
                },
                use_container_width=True
            )
            
      
        else:
            st.info("No active open ETF records found in your current broker summary file.")
    else:
        st.info("Please upload broker sheets via the sidebar to populate active index assets.")
        
    st.markdown("---")
    
    # ------------------------------------------------------------------------------
    # POSITION GRID 2: REALIZED HISTORICAL ETF CLOSURES (BOTTOM ROW BLOCK)
    # ------------------------------------------------------------------------------
    st.markdown("### 🏆 Historical Closed ETF Executions")
    st.markdown("Completed historical liquidations for index tracking assets archived in your local SQL database.")
    
    if not df_sql_closed.empty:
        df_close_etf = df_sql_closed[df_sql_closed['is_etf'] == 1].copy()
        if not df_close_etf.empty:

    # Local Summary KPI Blocks for Closed Executions
            closed_etf_pnl = df_close_etf['profit_loss'].sum()
            closed_etf_wins = len(df_close_etf[df_close_etf['profit_loss'] > 0])
            closed_etf_total_count = len(df_close_etf)
            closed_etf_accuracy = (closed_etf_wins / closed_etf_total_count * 100) if closed_etf_total_count > 0 else 0.0
            closed_etf_pnl_pct = (closed_etf_pnl / df_close_etf['buy_value'].sum() * 100) if df_close_etf['buy_value'].sum() > 0 else 0.0
            
            ckpi1, ckpi2, ckpi3 = st.columns(3)
            ckpi1.metric("Realized ETF Book Net Profit", f"₹ {closed_etf_pnl:,.2f}")
            ckpi2.metric("ETF Trade Win Accuracy", f"{closed_etf_accuracy:.1f}%", f"{closed_etf_total_count} Executions Logged")
            ckpi3.metric("Realized P&L %", f"{closed_etf_pnl_pct:.2f}%")

            # Calculate locked-in return percentage metrics based on initial trade buy values
            df_close_etf['realized_pnl_pct'] = np.where(
                df_close_etf['buy_value'] > 0,
                (df_close_etf['profit_loss'] / df_close_etf['buy_value']) * 100,
                0.0
            )
            
            # Format historical display ledger grid
            st.dataframe(
                df_close_etf[['stock_symbol', 'buy_date', 'sell_date', 'hold_days', 'buy_qty', 'profit_loss', 'realized_pnl_pct']], 
                column_config={
                    "stock_symbol": st.column_config.TextColumn("ETF Symbol Ticker"),
                    "buy_date": st.column_config.TextColumn("Buy Timeline Date"),
                    "sell_date": st.column_config.TextColumn("Sell Settlement Date"),
                    "hold_days": st.column_config.NumberColumn("Duration Days", format="%d Days"),
                    "buy_qty": st.column_config.NumberColumn("Traded Qty"),
                    "profit_loss": st.column_config.NumberColumn("Realized P&L Amount", format="₹%.2f"),
                    "realized_pnl_pct": st.column_config.NumberColumn("Realized P&L %", format="%.2f%%")
                },
                use_container_width=True
            )
            
        
        else:
            st.info("No historical closed ETF transactions archived in your database table logs.")
    else:
        st.info("Please upload broker sheets to populate closed investment performance curves.")


# ----------------------------------------------------------------------------------
# ACTIVE SWING BOOK (Tab 3 - UPDATED WITH STRATEGIC INSIGHTS DASHBOARD)
# ----------------------------------------------------------------------------------
with tab_swing:
    st.subheader("⏳ Short-Term Swing Book Positions (Stocks Only)")
    st.markdown("This tab displays active short-term holdings, strategic accuracy tracking, and recently closed trades held for **60 days or less**.")
    
    # Isolate equity transactions by removing asset-allocation index pools
    df_o_stocks = df_sql_open[df_sql_open['is_etf'] == 0].copy() if not df_sql_open.empty else pd.DataFrame()
    df_c_stocks = df_sql_closed[df_sql_closed['is_etf'] == 0].copy() if not df_sql_closed.empty else pd.DataFrame()

    # ------------------------------------------------------------------------------
    # NEW INSERT: STRATEGIC INSIGHTS PERFORMANCE DASHBOARD
    # ------------------------------------------------------------------------------
   
    st.markdown("### 📊 Strategy Performance Analytics")
    
    if not df_c_stocks.empty:
        # Standardize closed trade column data mapping types
        df_analytics = df_c_stocks.copy()
        df_analytics['hold_days'] = pd.to_numeric(df_analytics['hold_days'], errors='coerce').fillna(0)
        df_analytics['profit_loss'] = pd.to_numeric(df_analytics['profit_loss'], errors='coerce').fillna(0.0)
        df_analytics['sell_date_dt'] = pd.to_datetime(df_analytics['sell_date'], errors='coerce')
        
        # Isolate closed trades held for 60 days or less (Swing Definition)
        df_swing_analytics = df_analytics[df_analytics['hold_days'] <= 60].copy()
        
        if not df_swing_analytics.empty:
            # Map structural strategy parameters to historic closed trades using our master ticker profile metadata definitions
            conn = sqlite3.connect(DB_FILE)
            df_strat_meta = pd.read_sql_query("SELECT DISTINCT stock_name, strategy FROM portfolio_snapshots", conn)
            conn.close()
            
            strat_mapping = dict(zip(df_strat_meta['stock_name'].str.upper(), df_strat_meta['strategy']))
            df_swing_analytics['Strategy_Label'] = df_swing_analytics['stock_symbol'].str.upper().map(strat_mapping).fillna("Unassigned / General Swing")
            
            # --- VIEW PANEL MULTI-CHOICE DROPDOWN FILTER ---
            all_strategies = df_swing_analytics['Strategy_Label'].unique().tolist()
            selected_strategies = st.multiselect(
                "Filter Charts by Swing Strategy Profile:",
                options=all_strategies,
                default=all_strategies
            )
            
            # Slice metrics rows based on selector keys
            df_filtered_strat = df_swing_analytics[df_swing_analytics['Strategy_Label'].isin(selected_strategies)].copy()
            
            if not df_filtered_strat.empty:
                # --- VISUAL FRAME 1: STRATEGY WIN METRICS MATRIX ---
                st.markdown("#### 🎯 Strategy Win Metrics & Expectancy")
                
                strat_summary_rows = []
                for strat_name, group in df_filtered_strat.groupby('Strategy_Label'):
                    total_trades = len(group)
                    winning_trades = len(group[group['profit_loss'] > 0])
                    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
                    net_returns = group['profit_loss'].sum()
                    avg_hold_days = group['hold_days'].mean()
                    
                    strat_summary_rows.append({
                        "Swing Strategy Profile": strat_name,
                        "Total Closed Trades": int(total_trades),
                        "Win Rate Accuracy %": f"{win_rate:.1f}%",
                        "Cumulative Net P&L (₹)": f"₹ {net_returns:,.2f}",
                        "Avg Holding Duration": f"{avg_hold_days:.1f} Days"
                    })
                
                st.table(pd.DataFrame(strat_summary_rows))
                
                # --- VISUAL FRAME 2: CHARTS ROW (TRAJECTORY AND DAILY P&L SPIKES) ---
                chart_row1, chart_row2 = st.columns(2)
                
                with chart_row1:
                    st.markdown("#### 📈 Cumulative Realized Equity Trajectory")
                    # Group by sell timeline dates to map compounding return step curves
                    df_filtered_strat = df_filtered_strat.sort_values(by='sell_date_dt')
                    df_filtered_strat['cumulative_pnl'] = df_filtered_strat.groupby('Strategy_Label')['profit_loss'].cumsum()
                    
                    fig_strat_curve = px.line(
                        df_filtered_strat,
                        x='sell_date_dt',
                        y='cumulative_pnl',
                        color='Strategy_Label',
                        labels={'sell_date_dt': 'Exit Closing Date', 'cumulative_pnl': 'Realized Growth Curve (₹)'},
                        title='Compounding Realized Returns by Trading Style',
                        markers=True
                    )
                    fig_strat_curve.update_layout(hovermode="x unified", plot_bgcolor="rgba(0,0,0,0)", xaxis_gridcolor="#eeeeee", yaxis_gridcolor="#eeeeee")
                    st.plotly_chart(fig_strat_curve, use_container_width=True)
                    
                with chart_row2:
                    st.markdown("#### ⚡ Daily Realized P&L Spike Distribution")
                    # Sum up individual daily results to catch cluster volatility sessions
                    df_spikes = df_filtered_strat.groupby(['sell_date_dt', 'Strategy_Type' if 'Strategy_Type' in df_filtered_strat else 'Strategy_Label'])['profit_loss'].sum().reset_index()
                    
                    fig_spikes = px.bar(
                        df_spikes,
                        x='sell_date_dt',
                        y='profit_loss',
                        color='sell_date_dt', # Generates interactive variance color bars
                        labels={'sell_date_dt': 'Trading Session Date', 'profit_loss': 'Net Realized Realization Spike (₹)'},
                        title='Daily Net P&L Profit/Loss Spike Sessions',
                        color_continuous_scale=px.colors.diverging.Tealrose
                    )
                    fig_spikes.update_layout(plot_bgcolor="rgba(0,0,0,0)", xaxis_gridcolor="#eeeeee", yaxis_gridcolor="#eeeeee")
                    st.plotly_chart(fig_spikes, use_container_width=True)
            else:
                st.warning("Select at least one strategy profile key block from the multiselect menu to draw graphs.")
        else:
            st.info("No closed short-term swing trades (≤ 60 days) found in history to calculate advanced strategic insights.")
    else:
        st.info("Upload completed transaction files via the sidebar to populate strategy performance trackers.")
        
    st.markdown("---")
   
    # ------------------------------------------------------------------------------
    # POSITION GRID 1: ACTIVE FLOATING POSITION SLICES (TOP BLOCKS - EDITABLE)
    # ------------------------------------------------------------------------------

    st.markdown("### 📊 Active Open Swing Trades")
    st.markdown("Enter your **Buy Date** in `YYYY-MM-DD` format.")
    
    if not df_o_stocks.empty:
        # 1. READ PERSISTED METADATA VALS DIRECTLY FROM PORTFOLIO SNAPSHOTS
        conn = sqlite3.connect(DB_FILE)
        df_db_meta = pd.read_sql_query("""
            SELECT stock_name, strategy, notes_reference, buy_date_manual 
            FROM portfolio_snapshots 
            WHERE snapshot_date = (SELECT max(snapshot_date) FROM portfolio_snapshots)
        """, conn)
        conn.close()


        meta_strat_map, meta_notes_map, meta_date_map = load_all_metadata_from_supabase()
           
        # 2. DYNAMICALLY COMPUTE HOLD DAYS AND EVALUATE CHOSEN STRATEGY TIME WINDOWS
        df_o_stocks['Buy Date'] = df_o_stocks['stock_name'].map(meta_date_map).fillna("")
        hold_days_list = []
        current_date_anchor = datetime.today().date()
        
        for idx, row in df_o_stocks.iterrows():
            manual_date_str = str(row['Buy Date']).strip()
            try:
                # If a valid date string exists, calculate running elapsed holding duration
                if manual_date_str and manual_date_str != "None" and len(manual_date_str) == 10:
                    parsed_buy_dt = pd.to_datetime(manual_date_str).date()
                    days_elapsed = (current_date_anchor - parsed_buy_dt).days
                    hold_days_list.append(max(0, days_elapsed))
                else:
                    hold_days_list.append(None) # Flag missing entries as None
            except Exception:
                hold_days_list.append(None)
                
        df_o_stocks['hold_days'] = hold_days_list

        # --- NEW INSERT: COMPUTE LIVE UNREALIZED P/L PERCENTAGE YIELDS ---
        # Guard against division-by-zero errors using np.where for empty accounts
        df_o_stocks['unrealized_pnl_pct'] = np.where(
            df_o_stocks['value_at_cost'] > 0,
            (df_o_stocks['unrealized_pnl'] / df_o_stocks['value_at_cost']) * 100,
            0.0
        )
        
        # 3. ENFORCE ADVANCED SWING FILTER: P&L <= 0 AND (Buy Date is Null/Empty OR Hold Days < 60)
        # We check for None/NaN or clean empty strings to trap unentered dates
        is_pnl_eligible = df_o_stocks['realized_pnl'] <= 0
        is_date_null = (df_o_stocks['Buy Date'] == "") | (df_o_stocks['Buy Date'].isna())
        is_under_60_days = df_o_stocks['hold_days'] < 60

        df_so = df_o_stocks[(df_o_stocks['realized_pnl'] <= 0) & (is_date_null | is_under_60_days)].copy()

        # Combine parameters into a structured compound slice
        df_so = df_o_stocks[(is_date_null | is_under_60_days)].copy()
        
        if not df_so.empty:
            # Map strategy and comment text metrics specifically for chosen filtered rows
            df_so['Strategy'] = df_so['stock_name'].map(meta_strat_map).fillna("Select Strategy")
            df_so['Notes Reference'] = df_so['stock_name'].map(meta_notes_map).fillna("")
            
            # Fill display NaNs to clean up number column formatting outputs
            df_so['hold_days'] = df_so['hold_days'].fillna(0).astype(int)
            
            # Structure display columns
            df_display = df_so[[
                'stock_name', 'Buy Date', 'hold_days', 'quantity', 'average_cost', 
                'value_at_cost', 'current_market_price', 'unrealized_pnl','unrealized_pnl_pct', 'Strategy', 'Notes Reference'
            ]].copy()
            
            # Render interactive edit interface canvas grid
            edited_df = st.data_editor(
                df_display,
                column_config={
                    "stock_name": st.column_config.TextColumn("Stock Name", disabled=True),
                    "Buy Date": st.column_config.TextColumn("Buy Date (YYYY-MM-DD)", default="YYYY-MM-DD", required=False),
                    "hold_days": st.column_config.NumberColumn("Hold Days", disabled=True, format="%d Days"),
                    "quantity": st.column_config.NumberColumn("Qty", disabled=True),
                    "average_cost": st.column_config.NumberColumn("Avg Cost", disabled=True, format="₹%.2f"),
                    "value_at_cost": st.column_config.NumberColumn("Value Cost", disabled=True, format="₹%.2f"),
                    "current_market_price": st.column_config.NumberColumn("CMP", disabled=True, format="₹%.2f"),
                    "unrealized_pnl": st.column_config.NumberColumn("Unrealized P&L", disabled=True, format="₹%.2f"),
                    "unrealized_pnl_pct": st.column_config.NumberColumn("Unrealized P&L %", disabled=True, format="%.2f%%"),

                    "Strategy": st.column_config.SelectboxColumn(
                        "Strategy",
                        options=["Select Strategy", "Breakout Trading", "Mean Reversion", "Momentum Chase", "Support Bounce", "Core Long Term Allocation"],
                        required=True
                    ),
                    "Notes Reference": st.column_config.TextColumn("Notes / Comments Reference", width="large")
                },
                use_container_width=True,
                height=750, 
                key="swing_open_editor",
                
            )
            
            if st.button("💾 Save Swing Operations Changes Permanently"):
                with st.spinner("Writing entries securely to your cloud database..."):
                    success = True
                    for idx, row in edited_df.iterrows():
                        if not save_metadata_to_supabase(row['stock_name'], row['Strategy'], row['Notes Reference'], row['Buy Date']):
                            success = False
                    if success:
                        st.success("🚀 Journal data backed up permanently to your Supabase Cloud Database!")
                        st.rerun()
                    else:
                        st.error("Database connection timeout. Check your connection string configurations.")


                #st.rerun()

        else:
            st.info("No active open stock swing trades identified in your active inventory.")
    else:
        st.info("Please ingest broker sheets via the sidebar to populate active open swing logs.")
        st.markdown("---")
    
    # ------------------------------------------------------------------------------
    # POSITION GRID 2: REALIZED HISTORICAL PERFORMANCE LOGS (BOTTOM BLOCKS)
    # ------------------------------------------------------------------------------
    st.markdown("### 🏆 Realized Closed Swing Book (Held ≤ 60 Days)")
    st.markdown("Completed transaction metrics for short-term positions closed within the 60-day parameter window.")
    
    if not df_c_stocks.empty:
        # Filter for historical closed trades held for 60 days or less
        df_sc = df_c_stocks[df_c_stocks['hold_days'] <= 60]
        if not df_sc.empty:
            # Display localized metrics right below the table
            st.metric(
                label="💰 Swing Book Realized Net Return", 
                value=f"₹ {df_sc['profit_loss'].sum():,.2f}",
                delta=f"{len(df_sc)} Trades Logged"
            )
            st.dataframe(
                df_sc[['stock_symbol', 'buy_date', 'sell_date', 'hold_days', 'buy_qty', 'profit_loss', 'sector']], 
                use_container_width=True
            )
            
            
        else:
            st.info("No historical short-term swing transactions archived in your database ledger.")
    else:
        st.info("Please upload broker sheets to populate closed swing history metrics.")

# ----------------------------------------------------------------------------------
# CORE LONG-TERM BOOK (Stocks Only - INTERACTIVE EDITABLE DATA LAYOUT)
# ----------------------------------------------------------------------------------
with tab_lt:
    st.subheader("🏆 Strategic Long-Term Book Core (Stocks Only)")
    st.markdown("This tab displays active long-term core investment allocations and closed investments held for **more than 60 days**.")
    
    # Isolate equity transactions by removing asset-allocation index pools
    df_o_stocks = df_sql_open[df_sql_open['is_etf'] == 0].copy() if not df_sql_open.empty else pd.DataFrame()
    df_c_stocks = df_sql_closed[df_sql_closed['is_etf'] == 0].copy() if not df_sql_closed.empty else pd.DataFrame()
    
       # ------------------------------------------------------------------------------
    # POSITION GRID 1: CORE ACTIVE INVESTMENT ALLOCATIONS (TOP BLOCKS - EDITABLE)
    # ------------------------------------------------------------------------------
    st.markdown("### 📊 Core Active Long-Term Open Equity")
    st.markdown("Displays core investment positions where the holding duration has reached **60 days or more**.")
    
    if not df_o_stocks.empty:
        # 1. READ PERSISTED METADATA REVIEWS DIRECTLY FROM PORTFOLIO SNAPSHOTS
        conn = sqlite3.connect(DB_FILE)
        df_db_meta_lt = pd.read_sql_query("""
            SELECT stock_name, strategy, notes_reference, buy_date_manual 
            FROM portfolio_snapshots 
            WHERE snapshot_date = (SELECT max(snapshot_date) FROM portfolio_snapshots)
        """, conn)
        conn.close()
        
      # FETCH CENTRALIZED JOURNALS OUT OF PERMANENT CLOUD DATABASE STORAGE
        meta_strat_map, meta_notes_map, meta_date_map = load_all_metadata_from_supabase()
        
        # Assign basic values to the main dataframe first to evaluate conditions
        df_o_stocks['Buy Date'] = df_o_stocks['stock_name'].map(meta_date_map).fillna("")
        
        # 2. DYNAMICALLY COMPUTE HOLD DAYS BASED ON MANUALLY ENTERED VALUES
        hold_days_list_lt = []
        current_date_anchor_lt = datetime.today().date()
        
        for idx, row in df_o_stocks.iterrows():
            manual_date_str = str(row['Buy Date']).strip()
            try:
                # If a valid date string exists, calculate running elapsed holding duration
                if manual_date_str and manual_date_str != "None" and len(manual_date_str) == 10:
                    parsed_buy_dt = pd.to_datetime(manual_date_str).date()
                    days_elapsed = (current_date_anchor_lt - parsed_buy_dt).days
                    hold_days_list_lt.append(max(0, days_elapsed))
                else:
                    hold_days_list_lt.append(None) # Flag missing entries as None
            except Exception:
                hold_days_list_lt.append(None)
                
        df_o_stocks['hold_days'] = hold_days_list_lt
        
        # 3. ENFORCE LONG-TERM CORE FILTER: P&L > 0 AND Buy Date is NOT Null AND Hold Days >= 60
        is_pnl_eligible_lt = df_o_stocks['realized_pnl'] > 0
        is_date_not_null = (df_o_stocks['Buy Date'] != "") & (df_o_stocks['Buy Date'].notna()) & (df_o_stocks['hold_days'].notna())
        is_60_days_or_more = df_o_stocks['hold_days'] >= 60
        
        # Combine parameters into a structured compound slice
        df_lo = df_o_stocks[(is_date_not_null & is_60_days_or_more)].copy()
        
        if not df_lo.empty:
            # Map strategy and comment text metrics specifically for filtered rows
            df_lo['Strategy'] = df_lo['stock_name'].map(meta_strat_map).fillna("Select Strategy")
            df_lo['Notes Reference'] = df_lo['stock_name'].map(meta_notes_map).fillna("")
            
            # Convert holding column values to integers for final table rendering
            df_lo['hold_days'] = df_lo['hold_days'].astype(int)

            # Guard against division-by-zero errors using np.where for clean safety boundaries
            df_lo['unrealized_pnl_pct'] = np.where(
                df_lo['value_at_cost'] > 0,
                (df_lo['unrealized_pnl'] / df_lo['value_at_cost']) * 100,
                0.0
            )

            # Structure display columns matching your design framework
            df_display_lt = df_lo[[
                'stock_name', 'Buy Date', 'hold_days', 'quantity', 'average_cost', 
                'value_at_cost', 'current_market_price', 'unrealized_pnl', 'unrealized_pnl_pct', 'Strategy', 'Notes Reference'
            ]].copy()
            
            # Render interactive edit interface canvas grid for long term investments
            edited_df_lt = st.data_editor(
                df_display_lt,
                column_config={
                    "stock_name": st.column_config.TextColumn("Stock Name", disabled=True),
                    "Buy Date": st.column_config.TextColumn("Buy Date (YYYY-MM-DD)", default="YYYY-MM-DD", disabled=True), # Lock it here to enforce Swing tab edits
                    "hold_days": st.column_config.NumberColumn("Hold Days", disabled=True, format="%d Days"),
                    "quantity": st.column_config.NumberColumn("Qty", disabled=True),
                    "average_cost": st.column_config.NumberColumn("Avg Cost", disabled=True, format="₹%.2f"),
                    "value_at_cost": st.column_config.NumberColumn("Value Cost", disabled=True, format="₹%.2f"),
                    "current_market_price": st.column_config.NumberColumn("CMP", disabled=True, format="₹%.2f"),
                    "unrealized_pnl_pct": st.column_config.NumberColumn("Unrealized P&L %", disabled=True, format="%.2f%%"),
                    "Strategy": st.column_config.SelectboxColumn(
                        "Strategy",
                        options=["Select Strategy", "Value Investing", "Growth Compounding", "Dividend Yield Core", "Strategic Core Asset Allocation"],
                        required=True
                    ),
                    "Notes Reference": st.column_config.TextColumn("Notes / Comments Reference", width="large")
                },
                use_container_width=True,
                key="lt_open_editor"
            )
            
            if st.button("💾 Save Long-Term Structural Updates"):
                conn = sqlite3.connect(DB_FILE)
                for idx, row in edited_df_lt.iterrows():
                    conn.execute("UPDATE portfolio_snapshots SET strategy = ?, notes_reference = ? WHERE stock_name = ?", (row['Strategy'], row['Notes Reference'], row['stock_name']))
                conn.commit()
                conn.close()
                
            if st.button("💾 Save Swing Operations Changes Permanently"):
                with st.spinner("Writing entries securely to your cloud database..."):
                    success = True
                    for idx, row in edited_df.iterrows():
                        if not save_metadata_to_supabase(row['stock_name'], row['Strategy'], row['Notes Reference'], row['Buy Date']):
                            success = False
                    if success:
                        st.success("🚀 Journal data backed up permanently to your Supabase Cloud Database!")
                        st.rerun()
                    else:
                        st.error("Database connection timeout. Check your connection string configurations.")


        else:
            st.info("No active open stock core investment holdings found matching the ≥ 60 days condition.")
    else:
        st.info("Please ingest broker sheets via the sidebar to populate active core investment logs.")
         
    st.markdown("---")
    
    # ------------------------------------------------------------------------------
    # POSITION GRID 2: REALIZED CORE CLOSURES HISTORY (BOTTOM BLOCKS)
    # ------------------------------------------------------------------------------
    st.markdown("### 🏆 Historical Realized Long-Term Closed Equity (> 60 Days)")
    st.markdown("Completed multi-month transactions that have been closed out of your active investment accounts.")
    
    if not df_c_stocks.empty:
        # Filter for historical closed trades held for more than 60 days
        df_lc = df_c_stocks[df_c_stocks['hold_days'] > 60]
        if not df_lc.empty:
              # Display localized metrics right below the table
            st.metric(
                label="💰 Long Term Book Realized P&L Total", 
                value=f"₹ {df_lc['profit_loss'].sum():,.2f}",
                delta=f"{len(df_lc)} Investments Closed"
            )
            st.dataframe(
                df_lc[['stock_symbol', 'buy_date', 'sell_date', 'hold_days', 'buy_qty', 'profit_loss', 'sector']], 
                use_container_width=True
            )
            
          
        else:
            st.info("No closed multi-month core stock positions archived in your database ledger.")
    else:
        st.info("Please upload broker sheets to populate closed long-term history metrics.")

# ----------------------------------------------------------------------------------
# HISTORICAL DATABASE LEDGER AUDITOR
# ----------------------------------------------------------------------------------
# ----------------------------------------------------------------------------------
# TAB 5: RAW DATABASE AUDITOR (APPENDED FOR ONE-CLICK BACKUPS)
# ----------------------------------------------------------------------------------
with tab_hist:
    st.subheader("🗄️ SQL Local Storage Database Auditor")
    choice = st.radio("Select Table:", ["Completed Realized Ledger Records", "Daily Portfolio Snapshots History"])
    if choice == "Completed Realized Ledger Records":
        st.dataframe(df_sql_closed, use_container_width=True)
    else:
        conn = sqlite3.connect(DB_FILE)
        st.dataframe(pd.read_sql_query("SELECT * FROM portfolio_snapshots ORDER BY snapshot_date DESC", conn), use_container_width=True)
        conn.close()

    # ------------------------------------------------------------------------------
    # NEW MANUAL DATA ARCHIVE EXPORT COMPONENT
    # ------------------------------------------------------------------------------
    st.markdown("---")
    st.subheader("📥 Manual Ledger Data Export Backup")
    st.markdown("Click the button below to download a clean, updated binary snapshot copy of your `trade_ledger.db` file directly to your personal computer.")
    
    try:
        # Open and load the binary database snapshot stream payload
        with open(DB_FILE, "rb") as db_file:
            db_bytes = db_file.read()
            
        st.download_button(
            label="💾 Download trade_ledger.db Backup File",
            data=db_bytes,
            file_name="trade_ledger.db",
            mime="application/x-sqlite3",
            use_container_width=True
        )
    except Exception as e:
        st.error(f"Failed to compile database download snapshot: {str(e)}")
