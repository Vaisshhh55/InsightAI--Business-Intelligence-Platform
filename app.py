import io
import os
import re
import difflib
import math
import sqlite3
from datetime import datetime, timedelta
from functools import wraps

import numpy as np
import pandas as pd
from flask import Flask, jsonify, redirect, render_template, request, send_file, session, url_for, g
from werkzeug.security import generate_password_hash, check_password_hash
from flask_jwt_extended import (
    JWTManager,
    create_access_token,
    jwt_required,
    get_jwt_identity,
    verify_jwt_in_request,
)
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from sklearn.linear_model import LinearRegression
from werkzeug.utils import secure_filename
import bleach

app = Flask(__name__)
# Ensure strong secrets in production; prefer environment variables.
flask_secret = os.getenv("FLASK_SECRET_KEY")
jwt_secret = os.getenv("JWT_SECRET_KEY")
import secrets as _secrets

# In production require explicit secrets and sufficient JWT key length
if os.getenv("ENV", "development").lower() == "production":
    if not flask_secret:
        raise RuntimeError("FLASK_SECRET_KEY environment variable must be set in production.")
    if not jwt_secret or len(jwt_secret.encode("utf-8")) < 32:
        raise RuntimeError("JWT_SECRET_KEY must be set in production and be at least 32 bytes long.")
else:
    if not flask_secret:
        # generate a secure random secret for development if none provided
        flask_secret = _secrets.token_urlsafe(48)
    if not jwt_secret or len(jwt_secret.encode("utf-8")) < 32:
        # generate a dev JWT secret when missing or too short
        jwt_secret = _secrets.token_urlsafe(48)

app.secret_key = flask_secret
app.config["JWT_SECRET_KEY"] = jwt_secret
app.config["PERMANENT_SESSION_LIFETIME"] = int(os.getenv("SESSION_LIFETIME_SECONDS", 60 * 30))
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Only set cookie Secure when explicitly running in production
app.config["SESSION_COOKIE_SECURE"] = os.getenv("ENV", "development").lower() == "production"
jwt = JWTManager(app)


@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "geolocation=()"
    # CSP: allow self and HTTPS CDNs (Plotly served over HTTPS). Keep style inline for templates.
    csp = "default-src 'self' https:; script-src 'self' https:; style-src 'self' 'unsafe-inline' https:; img-src 'self' data: https:;"
    response.headers["Content-Security-Policy"] = csp
    # HSTS only in production
    if os.getenv("ENV", "development").lower() == "production":
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    return response


# Convert PERMANENT_SESSION_LIFETIME if set as seconds
try:
    sess_seconds = int(app.config.get("PERMANENT_SESSION_LIFETIME", 1800))
    app.permanent_session_lifetime = timedelta(seconds=sess_seconds)
except Exception:
    app.permanent_session_lifetime = timedelta(minutes=30)

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
REPORT_FOLDER = os.path.join(os.path.dirname(__file__), "reports")
DB_PATH = os.path.join(os.path.dirname(__file__), "insightai.db")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(REPORT_FOLDER, exist_ok=True)

DATASETS = {}

# Limit uploads to 10MB
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024


def get_db_connection():
    database_path = app.config.get("DATABASE_PATH", DB_PATH)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    connection = get_db_connection()
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT DEFAULT 'user',
            created_at TEXT NOT NULL
        )
        """
    )
    # Ensure role column exists for older DBs
    try:
        connection.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'")
    except Exception:
        pass

    # Table to persist uploaded dataset metadata
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS datasets (
            dataset_id TEXT PRIMARY KEY,
            filename TEXT,
            path TEXT,
            row_count INTEGER,
            column_count INTEGER,
            quality_score INTEGER,
            created_by INTEGER,
            created_at TEXT
        )
        """
    )

    # Chat history for assistant per dataset/user
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset_id TEXT,
            user_id INTEGER,
            role TEXT,
            message TEXT,
            response TEXT,
            created_at TEXT
        )
        """
    )

    # Persisted reports history
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset_id TEXT,
            filename TEXT,
            path TEXT,
            format TEXT,
            created_by INTEGER,
            created_at TEXT
        )
        """
    )
    # Aggregations for efficient server-side filtering
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS dataset_aggregations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset_id TEXT,
            period TEXT,
            metric_sum REAL,
            category TEXT,
            created_at TEXT
        )
        """
    )

    # audit logs
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT,
            target TEXT,
            details TEXT,
            created_at TEXT
        )
        """
    )
    connection.commit()
    connection.close()


init_db()


def login_required(view):
    @wraps(view)
    def protected(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api"):
                return jsonify({"error": "Authentication required."}), 401
            return redirect(url_for("landing_page"))
        return view(*args, **kwargs)

    return protected


def login_or_jwt_required(view):
    @wraps(view)
    def protected(*args, **kwargs):
        # Prefer session-based auth for browser flows
        user_id = session.get("user_id")
        if user_id:
            g.user_id = user_id
            return view(*args, **kwargs)

        # Try JWT access token (API clients)
        try:
            verify_jwt_in_request(optional=True)
            identity = get_jwt_identity()
            if identity:
                g.user_id = identity
                return view(*args, **kwargs)
        except Exception:
            pass

        if request.path.startswith("/api") or request.is_json:
            return jsonify({"error": "Authentication required."}), 401
        return redirect(url_for("landing_page"))

    return protected


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user_id = session.get('user_id') or getattr(g, 'user_id', None)
        if not user_id:
            return jsonify({'error': 'Authentication required.'}), 401
        conn = get_db_connection()
        user = conn.execute('SELECT role FROM users WHERE id = ?', (user_id,)).fetchone()
        conn.close()
        role = user['role'] if user and 'role' in user.keys() else 'user'
        if role != 'admin':
            return jsonify({'error': 'Admin role required.'}), 403
        return view(*args, **kwargs)

    return wrapped


# Simple in-memory rate limiter for admin endpoints (per-IP, per-endpoint).
# Note: this is process-local and intended as a basic safeguard only.
RATE_LIMIT = {}
def admin_rate_limit(limit=120, per_seconds=60):
    def deco(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            try:
                ip = request.headers.get('X-Forwarded-For') or request.remote_addr or 'unknown'
                key = (ip, func.__name__)
                entry = RATE_LIMIT.get(key)
                now = datetime.utcnow()
                if not entry:
                    RATE_LIMIT[key] = [1, now]
                else:
                    count, ts = entry
                    if (now - ts).total_seconds() > per_seconds:
                        RATE_LIMIT[key] = [1, now]
                    else:
                        if count >= limit:
                            return jsonify({'error': 'Too many requests, slow down.'}), 429
                        RATE_LIMIT[key][0] = count + 1
                return func(*args, **kwargs)
            except Exception:
                return func(*args, **kwargs)

        return wrapped

    return deco


def normalize_column_name(column_name):
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(column_name).strip().lower()).strip("_")
    return cleaned or "column"


def clean_dataset(df):
    original = df.copy()
    cleaned = df.copy()

    cleaned.columns = [normalize_column_name(c) for c in cleaned.columns]
    cleaned = cleaned.dropna(how="all")
    cleaned = cleaned.drop_duplicates()

    duplicate_count = int(original.duplicated().sum())
    missing_before = int(original.isna().sum().sum())

    for column in cleaned.columns:
        column_name = str(column).lower()
        if pd.api.types.is_numeric_dtype(cleaned[column]):
            cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")
            median_value = cleaned[column].median()
            cleaned[column] = cleaned[column].fillna(median_value)
        elif pd.api.types.is_datetime64_any_dtype(cleaned[column]) or "date" in column_name or "time" in column_name:
            cleaned[column] = pd.to_datetime(cleaned[column], errors="coerce")
        else:
            cleaned[column] = cleaned[column].astype("string").fillna("Unknown")
            mode_value = cleaned[column].mode(dropna=True)
            if not mode_value.empty:
                cleaned[column] = cleaned[column].fillna(mode_value.iloc[0])

    for column in cleaned.select_dtypes(include=[np.number]).columns:
        if cleaned[column].nunique() > 2 and len(cleaned) <= 20000:
            std = float(cleaned[column].std())
            if np.isfinite(std) and std > 0:
                z_score = np.abs((cleaned[column] - cleaned[column].mean()) / std)
                outlier_mask = z_score > 3
                if outlier_mask.any():
                    cleaned.loc[outlier_mask, column] = cleaned[column].median()

    missing_after = int(cleaned.isna().sum().sum())
    quality_score = max(40, min(100, int(100 - (missing_before * 0.8) - (duplicate_count * 3) - (missing_after * 0.2))))

    return cleaned, quality_score, {
        "duplicates_removed": duplicate_count,
        "missing_before": missing_before,
        "missing_after": missing_after,
    }


def infer_target_column(cleaned_df):
    keywords = ["revenue", "sales", "profit", "amount", "price", "total", "value", "score"]
    for column in cleaned_df.columns:
        if any(keyword in column for keyword in keywords):
            return column
    for column in cleaned_df.columns:
        if pd.api.types.is_numeric_dtype(cleaned_df[column]):
            return column
    return cleaned_df.columns[0]


def detect_date_column(cleaned_df):
    for column in cleaned_df.columns:
        if "date" in column or "time" in column:
            return column
    return None


def prepare_analysis(cleaned_df, dataset_name, quality_score=0):
    target_column = infer_target_column(cleaned_df)
    date_column = detect_date_column(cleaned_df)
    sample_size = 2000 if len(cleaned_df) > 2000 else len(cleaned_df)
    analysis_frame = cleaned_df.sample(n=sample_size, random_state=42) if len(cleaned_df) > 2000 else cleaned_df.copy()

    metric_series = cleaned_df[target_column]
    if not pd.api.types.is_numeric_dtype(metric_series):
        metric_series = pd.to_numeric(metric_series, errors="coerce")
        cleaned_df[target_column] = metric_series

    total_value = float(metric_series.sum())
    avg_value = float(metric_series.mean())
    growth_rate = 0.0
    forecast_points = []
    time_series = []

    if date_column and pd.api.types.is_datetime64_any_dtype(cleaned_df[date_column]):
        time_frame = cleaned_df[[date_column, target_column]].copy()
        time_frame = time_frame.sort_values(date_column)
        time_frame = time_frame.groupby(pd.Grouper(key=date_column, freq="M")).sum().reset_index()
        if len(time_frame) >= 3:
            values = time_frame[target_column].to_numpy(dtype=float)
            if len(values) > 1:
                x_axis = np.arange(len(values)).reshape(-1, 1)
                try:
                    model = LinearRegression()
                    model.fit(x_axis, values)
                    future_points = model.predict(np.arange(len(values), len(values) + 3).reshape(-1, 1))
                    forecast_points = [max(0.0, round(float(v), 2)) for v in future_points]
                except Exception:
                    forecast_points = []
            time_series = [
                {"label": label.strftime("%b %Y"), "value": round(float(value), 2)}
                for label, value in zip(time_frame[date_column], time_frame[target_column])
            ]
            if len(time_series) >= 2:
                growth_rate = round(((time_series[-1]["value"] - time_series[0]["value"]) / max(1.0, time_series[0]["value"])) * 100, 2)
    elif len(metric_series) >= 2:
        values = metric_series.to_numpy(dtype=float)
        first = values[0]
        last = values[-1]
        growth_rate = round(((last - first) / max(1.0, first)) * 100, 2)

    top_categories = []
    category_columns = [c for c in cleaned_df.columns if c != target_column and c != date_column and cleaned_df[c].dtype == "object"]
    if category_columns:
        category_column = category_columns[0]
        grouped = cleaned_df.groupby(category_column, dropna=False)[target_column].sum().sort_values(ascending=False)
        top_categories = [{"name": str(name), "value": round(float(value), 2)} for name, value in grouped.head(5).items()]

    explanatory_columns = [
        c for c in cleaned_df.columns if c not in {target_column, date_column}
        and pd.api.types.is_numeric_dtype(cleaned_df[c])
    ]
    feature_importance = []
    if len(explanatory_columns) >= 1:
        try:
            feature_frame = analysis_frame[[*explanatory_columns, target_column]].copy()
            feature_frame = feature_frame.dropna()
            if len(feature_frame) > 1:
                scores = []
                for column in explanatory_columns:
                    correlation = feature_frame[column].corr(feature_frame[target_column])
                    if pd.notna(correlation):
                        scores.append((column, abs(float(correlation))))
                ranked = sorted(scores, key=lambda item: item[1], reverse=True)[:3]
                feature_importance = [{"name": name, "score": round(float(score), 3)} for name, score in ranked]
        except Exception:
            feature_importance = []

    recommendations = []
    if growth_rate < 0:
        recommendations.append("Prioritize the strongest customer segments and protect margin on underperforming regions.")
    else:
        recommendations.append("Scale the channels that are already driving the strongest gains and monitor retention.")
    if quality_score < 80:
        recommendations.append("Tighten data quality rules around missing values and duplicate records before expanding the rollout.")
    else:
        recommendations.append("Use the current data quality score as a benchmark for executive reporting and governance.")

    return {
        "dataset_name": dataset_name,
        "row_count": int(cleaned_df.shape[0]),
        "column_count": int(cleaned_df.shape[1]),
        "metric_column": target_column,
        "metric_total": round(total_value, 2),
        "metric_average": round(avg_value, 2),
        "growth_rate": growth_rate,
        "forecast": forecast_points,
        "time_series": time_series,
        "top_categories": top_categories,
        "feature_importance": feature_importance,
        "recommendations": recommendations,
        "executive_narrative": build_executive_narrative({
            "row_count": int(cleaned_df.shape[0]),
            "column_count": int(cleaned_df.shape[1]),
            "quality_score": quality_score,
            "metric_column": target_column,
            "metric_total": round(total_value, 2),
            "metric_average": round(avg_value, 2),
            "growth_rate": growth_rate,
            "forecast": forecast_points,
            "dataset_name": dataset_name,
        }),
        "dataset_columns": list(cleaned_df.columns),
    }


def build_executive_narrative(analysis):
    trend_direction = "up" if analysis.get("growth_rate", 0) >= 0 else "down"
    forecast_summary = ""
    if analysis.get("forecast"):
        if len(analysis["forecast"]) > 1:
            forecast_summary = f" Forecasting indicates a projected range from {analysis['forecast'][0]} to {analysis['forecast'][-1]} across the next {len(analysis['forecast'])} periods."
        else:
            forecast_summary = f" Forecasting indicates an outlook of {analysis['forecast'][0]} for the next period."

    return (
        f"The dataset contains {analysis['row_count']} rows and {analysis['column_count']} columns with a {analysis['quality_score']}/100 data quality score. "
        f"The primary metric {analysis['metric_column']} totals {analysis['metric_total']} with an average of {analysis['metric_average']}, "
        f"and the trend is moving {trend_direction} by {abs(analysis.get('growth_rate', 0))}%."
        + forecast_summary
    )


def _map_columns(df):
    """Create a mapping from canonical business column names to actual dataframe columns.

    Uses fuzzy matching and common synonyms to find best fits.
    """
    # canonical names and synonyms
    synonyms = {
        'sales': ['sales', 'revenue', 'amount', 'total', 'order_value'],
        'profit': ['profit', 'margin', 'gross_profit'],
        'customer_name': ['customer name', 'client', 'buyer', 'customer'],
        'customer_id': ['customer id', 'client id', 'customerid', 'customer_id'],
        'product_name': ['product', 'product name', 'item', 'sku', 'product_name'],
        'category': ['category', 'product category', 'cat'],
        'sub_category': ['sub-category', 'sub category', 'subcategory', 'sub_category'],
        'region': ['region', 'area', 'territory'],
        'state': ['state', 'province'],
        'city': ['city', 'town'],
        'discount': ['discount', 'discount_pct', 'discount_percent'],
        'quantity': ['quantity', 'qty', 'units'],
        'order_date': ['order date', 'order_date', 'date', 'orderdate', 'timestamp'],
        'segment': ['segment', 'customer_segment'],
        'ship_mode': ['ship mode', 'shipping', 'ship_mode'],
    }

    cols = list(df.columns)
    lower_cols = {c.lower(): c for c in cols}
    mapping = {}
    for canon, keys in synonyms.items():
        best = None
        for key in keys:
            # direct match
            if key in lower_cols:
                best = lower_cols[key]
                break
        if best:
            mapping[canon] = best
            continue
        # fuzzy match using difflib
        candidates = difflib.get_close_matches(canon, [c.lower() for c in cols], n=1, cutoff=0.6)
        if candidates:
            mapping[canon] = lower_cols[candidates[0]]
            continue
        # try matching any synonym with fuzzy
        found = False
        for key in keys:
            candidates = difflib.get_close_matches(key, [c.lower() for c in cols], n=1, cutoff=0.6)
            if candidates:
                mapping[canon] = lower_cols[candidates[0]]
                found = True
                break
        if not found:
            mapping[canon] = None
    return mapping


def _detect_intent_and_entities(message):
    """Simple intent classifier using keywords and regex. Returns intent and detected entities."""
    m = message.lower()
    # order matters: more specific intents first to avoid matching generic 'sales' too early
    intents = {
        'product': ['top product', 'best selling', 'top selling', 'highest sales', 'most sold', 'product', 'sku'],
        'region': ['sales by region', 'by region', 'region sales', 'region', 'territory', 'state'],
        'repeat_customers': ['repeat customers', 'repeat buyers', 'returning customers', 'repeat purchases'],
        'churn': ['churn', 'customer churn', 'lost customers', 'attrition'],
        'cohort': ['cohort', 'cohort analysis', 'retention by cohort'],
        'cltv': ['lifetime value', 'cltv', 'ltv', 'customer lifetime value'],
        'forecasting': ['monthly sales', 'sales by month', 'per month', 'forecast', 'predict', 'projection'],
        'customer': ['top customer', 'top customers', 'highest spending customer', 'best customer', 'customer'],
        'sales': ['total sales', 'sum of sales', 'total revenue', 'sales total', 'sales'],
        'profit': ['total profit', 'profit total', 'profit', 'margin'],
        'dataset_information': ['summary', 'dataset summary', 'data summary', 'rows', 'columns', 'schema', 'fields', 'types'],
        'data_quality': ['quality', 'missing', 'duplicates', 'outlier', 'data quality'],
        'recommendations': ['recommend', 'what should', 'suggest', 'how can', 'which products should', 'improvements', 'opportunity'],
        'dashboard': ['dashboard', 'overview', 'visualization', 'charts', 'overview report'],
    }
    # match intent by keyword presence
    for intent, keys in intents.items():
        for k in keys:
            if k in m:
                # extract simple entities
                ents = {}
                mtop = re.search(r'top\s+(\d+)', m)
                if mtop:
                    try:
                        ents['top_n'] = int(mtop.group(1))
                    except Exception:
                        pass
                mnum = re.search(r'last\s+(\d+)\s+months', m) or re.search(r'past\s+(\d+)\s+months', m)
                if mnum:
                    try:
                        ents['months'] = int(mnum.group(1))
                    except Exception:
                        pass
                # promotion phrasing -> recommendations intent with promote type
                if 'promot' in m or 'should be promoted' in m or 'which products should' in m:
                    ents['recommendation_type'] = 'promote'
                    if 'top' not in ents:
                        ents['top_n'] = 5
                    return 'recommendations', ents
                # explicit 'which product has highest' -> top_n = 1
                if 'highest' in m or 'which product has highest' in m or 'which product has the highest' in m or 'highest sales' in m:
                    ents['top_n'] = 1
                    return intent, ents
                return intent, ents

    # fallback heuristics
    if re.search(r'top\s+\d+', m):
        return 'product', {'top_n': int(re.search(r'top\s+(\d+)', m).group(1))}
    if 'forecast' in m or 'predict' in m:
        return 'forecasting', {}
    # final fallback
    return 'unknown', {}


def _safe_percent(a, b):
    try:
        if b == 0:
            return 0.0
        return round(100.0 * a / b, 2)
    except Exception:
        return 0.0


def _format_currency(v):
    try:
        # use simple formatting; currency symbol is not enforced
        return f"{round(float(v), 2):,}"
    except Exception:
        return str(v)


def _answer_intent(intent, df, analysis, col_map, entities=None):
    """Execute dataframe operations for intents and build structured response."""
    rows = len(df)
    result = {"answer": None, "stats": {}, "explanation": None, "recommendation": None, "confidence": 0}

    sales_col = col_map.get('sales')
    profit_col = col_map.get('profit')
    date_col = col_map.get('order_date')

    # Dashboard: quick KPIs for overview requests
    if intent == 'dashboard':
        kpis = {}
        if sales_col and sales_col in df.columns:
            total_sales = float(df[sales_col].sum())
            kpis['total_sales'] = total_sales
            kpis['avg_order'] = float(df[sales_col].mean())
        if profit_col and profit_col in df.columns:
            kpis['total_profit'] = float(df[profit_col].sum())
        prod_col = col_map.get('product_name')
        if sales_col and prod_col and prod_col in df.columns:
            top_prod = df.groupby(prod_col)[sales_col].sum().sort_values(ascending=False).head(1)
            if not top_prod.empty:
                kpis['top_product'] = str(top_prod.index[0])
                kpis['top_product_sales'] = float(top_prod.iloc[0])
        region_col = col_map.get('region') or col_map.get('state')
        if sales_col and region_col and region_col in df.columns:
            top_region = df.groupby(region_col)[sales_col].sum().sort_values(ascending=False).head(1)
            if not top_region.empty:
                kpis['top_region'] = str(top_region.index[0])
                kpis['top_region_sales'] = float(top_region.iloc[0])
        # last month change if dates available
        if date_col and date_col in df.columns:
            try:
                s = df[[date_col, sales_col]].dropna()
                s[date_col] = pd.to_datetime(s[date_col], errors='coerce')
                s = s.dropna()
                monthly = s.groupby(pd.Grouper(key=date_col, freq='M'))[sales_col].sum().sort_index()
                if len(monthly) >= 2:
                    last = float(monthly.iloc[-1])
                    prev = float(monthly.iloc[-2])
                    kpis['last_month_sales'] = last
                    kpis['last_month_change_pct'] = _safe_percent(last - prev, prev)
            except Exception:
                pass
        result['answer'] = 'Key performance indicators generated.'
        result['stats'] = kpis
        result['explanation'] = 'KPIs computed directly from uploaded dataset for quick dashboard overview.'
        result['recommendation'] = None
        result['confidence'] = 90
        return result

    # Customer-focused analytics
    if intent == 'repeat_customers':
        cid = col_map.get('customer_id') or col_map.get('customer_name')
        if not cid or cid not in df.columns:
            result['answer'] = 'No customer identifier available to compute repeat customers.'
            result['confidence'] = 30
            return result
        order_counts = df.groupby(cid).size()
        repeat = int((order_counts > 1).sum())
        total_customers = int(order_counts.count())
        pct = _safe_percent(repeat, total_customers)
        result['answer'] = f"{repeat} repeat customers out of {total_customers} customers ({pct}% of customers)."
        result['stats'] = {'repeat_customers': repeat, 'total_customers': total_customers, 'percent_repeat': pct}
        result['explanation'] = 'Repeat customers are those with more than one recorded transaction in the dataset.'
        result['recommendation'] = None
        result['confidence'] = 88
        return result

    if intent == 'cltv':
        cid = col_map.get('customer_id') or col_map.get('customer_name')
        if not cid or cid not in df.columns or not sales_col or sales_col not in df.columns:
            result['answer'] = 'CLTV requires a customer identifier and a sales/revenue column.'
            result['confidence'] = 30
            return result
        cust_sales = df.groupby(cid)[sales_col].sum()
        avg_lifetime_value = float(cust_sales.mean())
        avg_orders = float(df.groupby(cid).size().mean())
        result['answer'] = f"Estimated average lifetime revenue per customer: {_format_currency(avg_lifetime_value)}"
        result['stats'] = {'avg_lifetime_value': avg_lifetime_value, 'avg_orders_per_customer': avg_orders}
        result['explanation'] = 'Simple CLTV estimate using historical total revenue per customer; does not include margins or retention modeling.'
        result['recommendation'] = None
        result['confidence'] = 70
        return result

    if intent == 'cohort':
        cid = col_map.get('customer_id') or col_map.get('customer_name')
        if not cid or cid not in df.columns or not date_col or date_col not in df.columns:
            result['answer'] = 'Cohort analysis requires customer identifier and date/order_date columns.'
            result['confidence'] = 30
            return result
        try:
            tmp = df[[cid, date_col]].dropna().copy()
            tmp[date_col] = pd.to_datetime(tmp[date_col], errors='coerce')
            tmp = tmp.dropna()
            tmp['cohort_month'] = tmp.groupby(cid)[date_col].transform('min').dt.to_period('M').astype(str)
            tmp['order_month'] = tmp[date_col].dt.to_period('M').astype(str)
            cohort_sizes = tmp.groupby('cohort_month')[cid].nunique().sort_index()
            # retention: count unique customers from each cohort active in subsequent months (first 6)
            cohorts = {}
            months = sorted(tmp['order_month'].unique())
            for cohort in cohort_sizes.index:
                cohort_members = set(tmp[tmp['cohort_month'] == cohort][cid].unique())
                retention = []
                for m in months[:6]:
                    active = len(cohort_members & set(tmp[tmp['order_month'] == m][cid].unique()))
                    retention.append(_safe_percent(active, len(cohort_members)))
                cohorts[cohort] = {'size': int(cohort_sizes[cohort]), 'retention_first_6_months_pct': retention}
            result['answer'] = 'Cohort analysis generated (sizes and first-6-month retention percentages by cohort).'
            result['stats'] = {'cohorts': cohorts}
            result['explanation'] = 'Assigned customers to cohorts by their first purchase month and computed simple month-over-month retention for six months.'
            result['recommendation'] = None
            result['confidence'] = 75
            return result
        except Exception:
            result['answer'] = 'Cohort analysis failed due to data issues.'
            result['confidence'] = 40
            return result

    if intent == 'churn':
        cid = col_map.get('customer_id') or col_map.get('customer_name')
        if not cid or cid not in df.columns or not date_col or date_col not in df.columns:
            result['answer'] = 'Churn estimation requires a customer identifier and date column.'
            result['confidence'] = 30
            return result
        try:
            tmp = df[[cid, date_col]].dropna().copy()
            tmp[date_col] = pd.to_datetime(tmp[date_col], errors='coerce')
            tmp = tmp.dropna()
            # use the last two months in data: churn = customers active in previous month but not in last month
            tmp['order_month'] = tmp[date_col].dt.to_period('M').astype(str)
            months = sorted(tmp['order_month'].unique())
            if len(months) < 2:
                result['answer'] = 'Not enough monthly history to compute churn.'
                result['confidence'] = 35
                return result
            prev = set(tmp[tmp['order_month'] == months[-2]][cid].unique())
            last = set(tmp[tmp['order_month'] == months[-1]][cid].unique())
            churned = len(prev - last)
            churn_rate = _safe_percent(churned, len(prev))
            result['answer'] = f"Estimated churn (previous month -> last month): {churned} customers churned ({churn_rate}%)."
            result['stats'] = {'churned_customers': churned, 'previous_month_customers': len(prev), 'churn_rate_pct': churn_rate}
            result['explanation'] = 'Churn computed as customers present in the prior month but absent in the most recent month.'
            result['recommendation'] = None
            result['confidence'] = 65
            return result
        except Exception:
            result['answer'] = 'Failed to compute churn due to data issues.'
            result['confidence'] = 40
            return result

    if intent == 'dataset_summary':
        missing = int(df.isna().sum().sum())
        duplicates = int(df.duplicated().sum())
        numeric_count = len(df.select_dtypes(include=[np.number]).columns)
        result['answer'] = f"Dataset contains {rows} rows and {len(df.columns)} columns."
        result['stats'] = {'rows': rows, 'columns': len(df.columns), 'missing_values': missing, 'duplicates': duplicates, 'numeric_columns': numeric_count}
        result['explanation'] = f"Detected {numeric_count} numeric columns. Data cleaning removed duplicates and filled or coerced missing values."
        result['recommendation'] = None
        result['confidence'] = 95
        return result

    if intent == 'data_quality':
        missing = int(df.isna().sum().sum())
        duplicates = int(df.duplicated().sum())
        outliers = 0
        numeric_cols = df.select_dtypes(include=[np.number]).columns
        for c in numeric_cols:
            std = df[c].std()
            if np.isfinite(std) and std > 0:
                z = np.abs((df[c] - df[c].mean()) / std)
                outliers += int((z > 3).sum())
        result['answer'] = f"Data quality: {missing} missing values, {duplicates} duplicate rows, {outliers} numeric outliers detected."
        result['stats'] = {'missing': missing, 'duplicates': duplicates, 'outliers': outliers}
        result['explanation'] = "Missing and duplicate counts computed after initial cleaning; outliers detected using a 3-sigma rule on numeric columns."
        result['recommendation'] = None
        result['confidence'] = 90
        return result

    if intent == 'sales':
        if not sales_col or sales_col not in df.columns:
            result['answer'] = "I could not find a sales/revenue column in the dataset."
            result['confidence'] = 30
            return result
        total = df[sales_col].sum()
        avg = df[sales_col].mean()
        result['answer'] = f"Total sales: {_format_currency(total)}"
        result['stats'] = {'total_sales': float(total), 'average_sales': float(avg)}
        result['explanation'] = f"Computed sum and mean over {rows} rows on column '{sales_col}'."
        result['recommendation'] = None
        result['confidence'] = 92
        return result

    if intent == 'product':
        prod_col = col_map.get('product_name')
        if not sales_col or not prod_col or prod_col not in df.columns:
            result['answer'] = "Required product or sales column not found to compute top products."
            result['confidence'] = 35
            return result
        grouped = df.groupby(prod_col)[sales_col].sum().sort_values(ascending=False)
        top_n = (entities or {}).get('top_n') or 5
        top = grouped.head(top_n)
        # format stats as list of tuples
        top_list = [(str(idx), float(v)) for idx, v in top.items()]
        if top_n == 1:
            # single top product: give detailed stats and share contribution
            name = str(top.index[0])
            value = float(top.iloc[0])
            total_sales = float(df[sales_col].sum())
            share = _safe_percent(value, total_sales)
            # include profit if available
            profit_col = col_map.get('profit')
            profit_info = None
            if profit_col and profit_col in df.columns:
                prod_profit = df[df[prod_col] == name][profit_col].sum()
                profit_info = float(prod_profit)
            parts = [f"Top product: {name}", f"Sales: {_format_currency(value)} ({share}% of total sales)"]
            if profit_info is not None:
                parts.append(f"Profit: {_format_currency(profit_info)}")
            result['answer'] = '. '.join(parts)
            result['stats'] = {'product': name, 'sales': value, 'share_pct': share, 'profit': profit_info}
            result['explanation'] = f"'{name}' has the highest total sales when aggregating column '{sales_col}' by '{prod_col}'."
            result['recommendation'] = None
            result['confidence'] = 92
            return result
        result['answer'] = f"Top {top_n} products by sales computed from the dataset."
        result['stats'] = {'top_products': top_list}
        result['explanation'] = f"Aggregated sales per product using column '{prod_col}' and ranked by total sales."
        result['recommendation'] = None
        result['confidence'] = 90
        return result

    if intent == 'forecasting':
        if not sales_col or not date_col or date_col not in df.columns:
            result['answer'] = "Monthly aggregation requires a date-like column and a sales column."
            result['confidence'] = 40
            return result
        try:
            series = df[[date_col, sales_col]].dropna()
            series[date_col] = pd.to_datetime(series[date_col], errors='coerce')
            series = series.dropna()
            series['period'] = series[date_col].dt.to_period('M').astype(str)
            grouped = series.groupby('period')[sales_col].sum().sort_index()
            result['answer'] = f"Monthly sales computed for {len(grouped)} periods."
            result['stats'] = {'monthly': {k: float(v) for k, v in grouped.to_dict().items()}}
            result['explanation'] = "Monthly sums produced by grouping the order/date column by month."
            result['recommendation'] = None
            result['confidence'] = 85
            return result
        except Exception:
            result['answer'] = "Failed to compute monthly sales due to date parsing issues."
            result['confidence'] = 40
            return result

    if intent == 'forecast_next':
        # kept for compatibility but map to forecasting
        intent = 'forecasting'
    if intent == 'forecasting':
        # simple linear trend on monthly totals
        if not sales_col or not date_col or date_col not in df.columns:
            result['answer'] = "Forecasting needs a date-like column and a sales column."
            result['confidence'] = 30
            return result
        series = df[[date_col, sales_col]].dropna()
        series[date_col] = pd.to_datetime(series[date_col], errors='coerce')
        series = series.dropna()
        series = series.groupby(pd.Grouper(key=date_col, freq='M'))[sales_col].sum().reset_index()
        series = series.dropna()
        if len(series) < 3:
            result['answer'] = "Not enough monthly history to produce a reliable forecast."
            result['confidence'] = 35
            return result
        try:
            X = np.arange(len(series)).reshape(-1, 1)
            y = series[sales_col].to_numpy(dtype=float)
            model = LinearRegression()
            model.fit(X, y)
            pred = model.predict(np.array([[len(series)]]))[0]
            conf = 80 if len(series) >= 12 else 60
            result['answer'] = f"Predicted next-month sales: {_format_currency(pred)}"
            result['stats'] = {'last_month': float(y[-1]), 'predicted_next': float(pred), 'points_used': len(series)}
            result['explanation'] = f"Linear trend fitted to {len(series)} monthly points; this is a simple short-term projection and does not account for seasonality."
            result['recommendation'] = None
            result['confidence'] = conf
            return result
        except Exception:
            result['answer'] = "Forecast generation failed due to modeling error."
            result['confidence'] = 40
            return result

    if intent == 'customer':
        cid = col_map.get('customer_id') or col_map.get('customer_name')
        if not sales_col or not cid or cid not in df.columns:
            result['answer'] = "Customer-level analysis requires a customer identifier and sales column."
            result['confidence'] = 35
            return result
        grouped = df.groupby(cid)[sales_col].sum().sort_values(ascending=False)
        top_n = (entities or {}).get('top_n') or 5
        top = grouped.head(top_n)
        top_list = [(str(idx), float(v)) for idx, v in top.items()]
        result['answer'] = f"Top {top_n} customers by total spend computed from the dataset."
        result['stats'] = {'top_customers': top_list}
        result['explanation'] = "Aggregated sales per customer identifier to identify highest spenders."
        result['recommendation'] = None
        result['confidence'] = 88
        return result

    if intent == 'profit':
        if not profit_col or profit_col not in df.columns:
            result['answer'] = "Profit column not found in dataset."
            result['confidence'] = 30
            return result
        total = df[profit_col].sum()
        result['answer'] = f"Total profit: {_format_currency(total)}"
        result['stats'] = {'total_profit': float(total)}
        result['explanation'] = "Summed the profit column across all rows."
        result['recommendation'] = None
        result['confidence'] = 88
        return result

    if intent == 'region':
        # aggregate sales by region if available
        region_col = col_map.get('region') or col_map.get('state') or col_map.get('city')
        if not sales_col or not region_col or region_col not in df.columns:
            # if user asked for most_profitable_category mapped here, try category
            cat = col_map.get('category')
            if profit_col and cat and cat in df.columns:
                grouped = df.groupby(cat)[profit_col].sum().sort_values(ascending=False)
                top = grouped.head(3)
                top_list = [(str(idx), float(v)) for idx, v in top.items()]
                result['answer'] = "Most profitable categories computed from dataset."
                result['stats'] = {'top_categories': top_list}
                result['explanation'] = "Aggregated profit per category to identify contribution to bottom-line."
                result['recommendation'] = None
                result['confidence'] = 88
                return result
            result['answer'] = "Region-level analysis requires a region/state/city column and a sales column."
            result['confidence'] = 35
            return result
        grouped = df.groupby(region_col)[sales_col].sum().sort_values(ascending=False)
        top = grouped.head(5)
        top_list = [(str(idx), float(v)) for idx, v in top.items()]
        result['answer'] = f"Sales by {region_col} computed; top regions returned."
        result['stats'] = {'top_regions': top_list}
        result['explanation'] = f"Aggregated sales per '{region_col}' to show regional performance."
        result['recommendation'] = None
        result['confidence'] = 90
        return result

    if intent == 'recommendations':
        # Generate explicit recommendations only when requested
        recs = []
        # recommend top SKUs to promote if sales exist
        rec_type = (entities or {}).get('recommendation_type')
        top_n = (entities or {}).get('top_n') or 5
        if rec_type == 'promote' or rec_type is None:
            if sales_col and sales_col in df.columns and (col_map.get('product_name') in df.columns if col_map.get('product_name') else False):
                prod_col = col_map.get('product_name')
                grouped = df.groupby(prod_col)[sales_col].sum().sort_values(ascending=False)
                top_prod = grouped.head(top_n)
                # include profit metric per product if available
                promo_items = []
                for name, val in top_prod.items():
                    p = {'product': str(name), 'sales': float(val)}
                    profit_col = col_map.get('profit')
                    if profit_col and profit_col in df.columns:
                        p['profit'] = float(df[df[prod_col] == name][profit_col].sum())
                        p['margin_pct'] = _safe_percent(p['profit'], p['sales'])
                    promo_items.append(p)
                if promo_items:
                    recs.append({'type': 'promote_products', 'items': promo_items})
        # recommend reviewing low-margin categories if profit exists
        if profit_col and profit_col in df.columns and col_map.get('category') and col_map.get('category') in df.columns:
            low_margin = df.groupby(col_map.get('category'))[profit_col].sum().sort_values().head(3)
            recs.append({'type': 'review_categories', 'items': [str(i) for i in low_margin.index[:3]]})
        # operational recommendations based on data quality
        missing = int(df.isna().sum().sum())
        if missing > 0:
            recs.append({'type': 'data_quality', 'items': [f'{missing} missing values detected']})
        # Build human-readable recommendation text based on recs
        result['answer'] = "Recommendations generated from the uploaded dataset (explicit request)."
        result['stats'] = {'recommendation_count': len(recs)}
        result['explanation'] = "These recommendations are derived from aggregations on sales, profit, and basic data quality checks."
        rec_text_parts = []
        for r in recs:
            if r['type'] == 'promote_products':
                items = r['items']
                lines = []
                for it in items:
                    if isinstance(it, dict):
                        s = f"{it['product']} (sales: {_format_currency(it['sales'])}"
                        if 'profit' in it and it['profit'] is not None:
                            s += f", profit: {_format_currency(it['profit'])}, margin: {it.get('margin_pct', 0)}%"
                        s += ")"
                        lines.append(s)
                    else:
                        lines.append(str(it))
                rec_text_parts.append("Promote these products: " + ", ".join(lines) + ".")
            if r['type'] == 'review_categories':
                rec_text_parts.append(f"Review pricing/discounts for categories: {', '.join(r['items'])}.")
            if r['type'] == 'data_quality':
                rec_text_parts.append(f"Data quality: {', '.join(r['items'])}.")
        result['recommendation'] = ' '.join(rec_text_parts) if rec_text_parts else None
        result['confidence'] = 75
        return result

    # Generic handler for unknown or open-ended questions: try to infer referenced columns and run data-driven summaries
    if intent == 'unknown':
        msg = analysis.get('last_user_message') if analysis.get('last_user_message') else ''
        # fallback to provided message variable if available
        try:
            # sometimes analysis may not carry message; use a safe global look-up via closure
            msg = msg or ''
        except Exception:
            msg = ''

        # find referenced columns via fuzzy matching of n-grams
        def find_columns_in_text(text):
            cols = list(df.columns)
            lower_cols = [c.lower() for c in cols]
            tokens = re.findall(r"[a-z0-9_]+", text.lower())
            found = set()
            # try 3-grams, 2-grams, 1-grams
            for n in (3, 2, 1):
                for i in range(len(tokens) - n + 1):
                    phrase = ' '.join(tokens[i : i + n])
                    matches = difflib.get_close_matches(phrase, lower_cols, n=1, cutoff=0.8)
                    if matches:
                        idx = lower_cols.index(matches[0])
                        found.add(cols[idx])
            # also try mapping canonical names
            for k, v in col_map.items():
                if v and v in cols:
                    if k in text.lower():
                        found.add(v)
            return list(found)

        # use both the user's message and any last_user_message stored in analysis
        text_to_scan = msg or ''
        # attempt to extract message from analysis if not set
        text_to_scan = text_to_scan.strip()
        found_cols = find_columns_in_text(text_to_scan)

        # if none found, try to use entire set of column names as possible intent
        if not found_cols:
            # consider presence of words like 'sales' or 'profit' in text
            if 'sales' in text_to_scan or 'revenue' in text_to_scan:
                if col_map.get('sales'):
                    found_cols = [col_map.get('sales')]
            if 'profit' in text_to_scan and col_map.get('profit'):
                found_cols = [col_map.get('profit')]

        # if still nothing and user included no message (rare), return available columns
        if not found_cols:
            sample_cols = list(df.columns)[:10]
            result['answer'] = "I couldn't identify which columns you meant. Available columns include: " + ", ".join(sample_cols)
            result['stats'] = {'available_columns_count': len(df.columns), 'sample_columns': sample_cols}
            result['explanation'] = 'Please ask about one or more columns by name (e.g., "total sales", "profit by category", "top 5 products").'
            result['confidence'] = 25
            return result

        # If we found numeric columns, produce detailed numeric summaries
        numeric_cols = [c for c in found_cols if c in df.select_dtypes(include=[np.number]).columns]
        cat_cols = [c for c in found_cols if c not in numeric_cols]

        if numeric_cols and not cat_cols:
            stats = {}
            lines = []
            for c in numeric_cols:
                col_series = pd.to_numeric(df[c], errors='coerce').dropna()
                stats[c] = {
                    'count': int(col_series.count()),
                    'sum': float(col_series.sum()),
                    'mean': float(col_series.mean()) if col_series.count() else None,
                    'median': float(col_series.median()) if col_series.count() else None,
                    'min': float(col_series.min()) if col_series.count() else None,
                    'max': float(col_series.max()) if col_series.count() else None,
                    'std': float(col_series.std()) if col_series.count() else None,
                }
                lines.append(f"Column '{c}': count={stats[c]['count']}, sum={_format_currency(stats[c]['sum'])}, mean={round(stats[c]['mean'] or 0,2)}")
            result['answer'] = "Numeric summary for requested columns: " + "; ".join(lines)
            result['stats'] = stats
            result['explanation'] = "Computed standard descriptive statistics on numeric columns mentioned in your question."
            result['recommendation'] = None
            result['confidence'] = 85
            return result

        # If category columns present and message includes 'by', attempt group-by
        if cat_cols:
            # detect 'by <col>' pattern
            m = re.search(r'by\s+([a-z0-9_ ]+)', text_to_scan)
            group_col = None
            if m:
                phrase = m.group(1).strip()
                # fuzzy match phrase to columns
                lower_cols = [c.lower() for c in df.columns]
                matches = difflib.get_close_matches(phrase, lower_cols, n=1, cutoff=0.6)
                if matches:
                    group_col = df.columns[lower_cols.index(matches[0])]
            if not group_col:
                group_col = cat_cols[0]

            # choose numeric columns to aggregate
            num_cols = list(df.select_dtypes(include=[np.number]).columns)
            if not num_cols:
                result['answer'] = f"Grouped counts by '{group_col}' computed (no numeric columns available to aggregate)."
                grouped = df.groupby(group_col).size().sort_values(ascending=False).head(10)
                result['stats'] = {'group_counts': grouped.to_dict()}
                result['explanation'] = f"Counted rows per '{group_col}'."
                result['confidence'] = 80
                return result

            agg = df.groupby(group_col)[num_cols].sum().sort_values(by=num_cols[0], ascending=False).head(10)
            # convert to simple dict of top groups with primary metric
            top = []
            for idx, row in agg.iterrows():
                primary = float(row[num_cols[0]])
                top.append((str(idx), primary))
            result['answer'] = f"Aggregated {len(num_cols)} numeric metrics by '{group_col}'. Showing top groups by {num_cols[0]}."
            result['stats'] = {'group_aggregates': top}
            result['explanation'] = f"Grouped dataset by '{group_col}' and summed available numeric metrics."
            result['recommendation'] = None
            result['confidence'] = 85
            return result

        # final fallback for unknown: show which columns matched
        result['answer'] = "I detected these relevant columns in your question: " + ", ".join(found_cols)
        result['stats'] = {'detected_columns': found_cols}
        result['explanation'] = "I can compute descriptive stats, group-bys, and simple forecasts when you specify the metric and grouping."
        result['confidence'] = 40
        return result

    # fallback
    result['answer'] = "I can answer business-analytics questions about this dataset (sales, profit, customers, products, region, forecasting)."
    result['confidence'] = 30
    return result


def assistant_reply(message, dataset):
    """Main assistant entrypoint: accepts raw message and dataset dict (with 'data' and 'analysis').

    Returns a structured textual reply built from real dataset analysis.
    """
    if not message or not message.strip():
        return "Please ask a question about the dataset and I'll help interpret it."

    df = None
    analysis = {}
    if isinstance(dataset, dict):
        df = dataset.get('data') if 'data' in dataset else None
        analysis = dataset.get('analysis', {})
    # if only analysis passed, we can't run queries
    if df is None or not hasattr(df, 'columns'):
        return "No dataset loaded or dataset not readable. Upload or load a dataset first."

    # ensure dataframe copy for safety
    try:
        df_proc = df.copy()
    except Exception:
        df_proc = df

    # canonical column mapping
    col_map = _map_columns(df_proc)

    # detect intent
    intent, entities = _detect_intent_and_entities(message)

    # run intent handler
    # normalize similar intents
    if intent == 'dataset_information':
        intent = 'dataset_summary'
    result = _answer_intent(intent, df_proc, analysis, col_map, entities)

    # Build reply string with required sections
    parts = []
    parts.append(f"Answer:\n{result.get('answer')}")
    stats = result.get('stats') or {}
    if stats:
        parts.append("\nSupporting statistics:")
        # format small table-like lines
        for k, v in list(stats.items())[:8]:
            try:
                if isinstance(v, dict):
                    parts.append(f"- {k}: {list(v.items())[:3]}")
                else:
                    parts.append(f"- {k}: {v}")
            except Exception:
                parts.append(f"- {k}: {v}")
    if result.get('explanation'):
        parts.append(f"\nBusiness explanation:\n{result.get('explanation')}")
    rec = result.get('recommendation') or result.get('recommendations')
    if rec:
        if isinstance(rec, list):
            rec_text = ' '.join(rec)
        else:
            rec_text = rec
        parts.append(f"\nRecommendation:\n{rec_text}")
    parts.append(f"\nConfidence:\n{result.get('confidence', 50)}%")

    return "\n\n".join(parts)


@app.route("/")
def landing_page():
    return render_template("landing.html")


@app.route("/workspace")
@login_required
def index():
    user = None
    user_id = session.get('user_id')
    if user_id:
        conn = get_db_connection()
        row = conn.execute("SELECT email, role FROM users WHERE id = ?", (user_id,)).fetchone()
        conn.close()
        if row:
            user = {
                'email': row['email'],
                'role': row['role'] if 'role' in row.keys() else 'user'
            }
    return render_template("index.html", user=user)


@app.route("/dashboard")
@login_required
def dashboard():
    user = None
    user_id = session.get('user_id')
    if user_id:
        conn = get_db_connection()
        row = conn.execute("SELECT email, role FROM users WHERE id = ?", (user_id,)).fetchone()
        conn.close()
        if row:
            user = {
                'email': row['email'],
                'role': row['role'] if 'role' in row.keys() else 'user'
            }
    return render_template("index.html", user=user)


@app.route("/auth/register", methods=["POST"])
def register_user():
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip().lower()
    password = (payload.get("password") or "").strip()
    if not email or not password:
        return jsonify({"error": "Email and password are required."}), 400

    connection = get_db_connection()
    existing = connection.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if existing:
        connection.close()
        return jsonify({"error": "User already exists."}), 409

    hashed = generate_password_hash(password)
    connection.execute(
        "INSERT INTO users (email, password, created_at) VALUES (?, ?, ?)",
        (email, hashed, datetime.utcnow().isoformat()),
    )
    connection.commit()
    user_id = connection.execute("SELECT last_insert_rowid() as id").fetchone()["id"]
    connection.close()
    session["user_id"] = user_id
    if "csrf_token" not in session:
        import secrets
        session["csrf_token"] = secrets.token_urlsafe(32)
    # audit
    try:
        conn = get_db_connection()
        conn.execute("INSERT INTO audit_logs (user_id, action, target, details, created_at) VALUES (?, ?, ?, ?, ?)", (user_id, 'register', user_id, 'user registered', datetime.utcnow().isoformat()))
        conn.commit()
        conn.close()
    except Exception:
        pass
    return jsonify({"message": "Registration successful.", "user_id": user_id})


@app.route("/auth/login", methods=["POST"])
def login_user():
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip().lower()
    password = (payload.get("password") or "").strip()
    if not email or not password:
        return jsonify({"error": "Email and password are required."}), 400
    connection = get_db_connection()
    user = connection.execute("SELECT id, email, password FROM users WHERE email = ?", (email,)).fetchone()
    connection.close()
    if not user or not check_password_hash(user["password"], password):
        return jsonify({"error": "Invalid credentials."}), 401

    session["user_id"] = user["id"]
    session.permanent = True
    # include role in session
    session["role"] = user["role"] if "role" in user.keys() else "user"
    if "csrf_token" not in session:
        import secrets
        session["csrf_token"] = secrets.token_urlsafe(32)
    token = create_access_token(identity=user["id"])
    # audit
    try:
        conn = get_db_connection()
        conn.execute("INSERT INTO audit_logs (user_id, action, target, details, created_at) VALUES (?, ?, ?, ?, ?)", (user['id'], 'login', user['id'], 'user login', datetime.utcnow().isoformat()))
        conn.commit()
        conn.close()
    except Exception:
        pass
    return jsonify({"message": "Login successful.", "user_id": user["id"], "access_token": token})


@app.route("/auth/logout", methods=["POST"])
def logout_user():
    session.pop("user_id", None)
    return jsonify({"message": "Logout successful."})


@app.route("/auth/session")
def session_status():
    if "user_id" in session:
        user_id = session["user_id"]
        try:
            conn = get_db_connection()
            user = conn.execute("SELECT id, email, role FROM users WHERE id = ?", (user_id,)).fetchone()
        finally:
            try:
                conn.close()
            except Exception:
                pass

        if user:
            return jsonify({"authenticated": True, "user": {"id": user["id"], "email": user["email"], "role": user["role"] if "role" in user.keys() else "user"}})
    return jsonify({"authenticated": False})


@app.route('/profile')
@login_required
def profile_page():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('landing_page'))
    conn = get_db_connection()
    user = conn.execute("SELECT id, email, role FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    if not user:
        return redirect(url_for('landing_page'))
    return render_template('profile.html', user={'email': user['email'], 'role': user['role'] if 'role' in user.keys() else 'user'})


@app.route("/upload", methods=["POST"])
@login_or_jwt_required
def upload_dataset():
    uploaded_file = request.files.get("file")
    if not uploaded_file or uploaded_file.filename == "":
        return jsonify({"error": "Please upload a file before analyzing it."}), 400

    filename = secure_filename(uploaded_file.filename)
    # Basic validation: allowed extensions
    allowed = {".csv", ".xls", ".xlsx"}
    suffix = os.path.splitext(filename)[1].lower()
    if suffix not in allowed:
        return jsonify({"error": "Only CSV and Excel files are supported."}), 400
    filename = bleach.clean(filename)
    file_path = os.path.join(UPLOAD_FOLDER, filename)
    uploaded_file.save(file_path)
    try:
        if suffix == ".csv":
            data = pd.read_csv(file_path)
        elif suffix == ".xlsx":
            data = pd.read_excel(file_path, nrows=20000, engine="openpyxl")
        elif suffix == ".xls":
            data = pd.read_excel(file_path, nrows=20000, engine="xlrd")
        else:
            return jsonify({"error": "Only CSV and Excel files are supported."}), 400
    except Exception as exc:
        return jsonify({"error": f"Unable to read the uploaded file: {exc}"}), 400

    cleaned, quality_score, details = clean_dataset(data)
    dataset_id = os.path.splitext(filename)[0]
    analysis = prepare_analysis(cleaned, dataset_id, quality_score)
    analysis["quality_score"] = quality_score
    analysis["details"] = details
    DATASETS[dataset_id] = {"path": file_path, "data": cleaned, "analysis": analysis}
    # persist dataset metadata
    try:
        conn = get_db_connection()
        conn.execute(
            "REPLACE INTO datasets (dataset_id, filename, path, row_count, column_count, quality_score, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                dataset_id,
                filename,
                file_path,
                int(cleaned.shape[0]),
                int(cleaned.shape[1]),
                int(quality_score),
                session.get("user_id"),
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass
    analysis["assistant_response"] = assistant_reply("Summarize the business performance", DATASETS[dataset_id])
    # Persist monthly aggregations for server-side filtering
    try:
        date_col = detect_date_column(cleaned)
        if date_col and pd.api.types.is_datetime64_any_dtype(cleaned[date_col]):
            # compute monthly sums
            monthly = cleaned[[date_col, analysis['metric_column']]].copy()
            monthly = monthly.dropna()
            monthly[date_col] = pd.to_datetime(monthly[date_col], errors='coerce')
            monthly = monthly.dropna()
            monthly['period'] = monthly[date_col].dt.to_period('M').astype(str)
            grouped = monthly.groupby('period')[analysis['metric_column']].sum().reset_index()
            conn = get_db_connection()
            for _, row in grouped.iterrows():
                try:
                    conn.execute(
                        "INSERT INTO dataset_aggregations (dataset_id, period, metric_sum, category, created_at) VALUES (?, ?, ?, ?, ?)",
                        (dataset_id, row['period'], float(row[analysis['metric_column']]), 'all', datetime.utcnow().isoformat()),
                    )
                except Exception:
                    pass
            conn.commit()
            conn.close()
    except Exception:
        pass
    # Persist monthly aggregations for server-side filtering
    try:
        date_col = detect_date_column(cleaned)
        if date_col and pd.api.types.is_datetime64_any_dtype(cleaned[date_col]):
            # compute monthly sums
            monthly = cleaned[[date_col, analysis['metric_column']]].copy()
            monthly = monthly.dropna()
            monthly[date_col] = pd.to_datetime(monthly[date_col], errors='coerce')
            monthly = monthly.dropna()
            monthly['period'] = monthly[date_col].dt.to_period('M').astype(str)
            grouped = monthly.groupby('period')[analysis['metric_column']].sum().reset_index()
            conn = get_db_connection()
            for _, row in grouped.iterrows():
                try:
                    conn.execute(
                        "INSERT INTO dataset_aggregations (dataset_id, period, metric_sum, category, created_at) VALUES (?, ?, ?, ?, ?)",
                        (dataset_id, row['period'], float(row[analysis['metric_column']]), 'all', datetime.utcnow().isoformat()),
                    )
                except Exception:
                    pass
            conn.commit()
            conn.close()
    except Exception:
        pass
    # audit
    try:
        conn = get_db_connection()
        conn.execute("INSERT INTO audit_logs (user_id, action, target, details, created_at) VALUES (?, ?, ?, ?, ?)", (session.get('user_id'), 'upload', dataset_id, f'rows:{cleaned.shape[0]} cols:{cleaned.shape[1]}', datetime.utcnow().isoformat()))
        conn.commit()
        conn.close()
    except Exception:
        pass
    # Save a data quality report file
    try:
        qreport_path = os.path.join(REPORT_FOLDER, f"{dataset_id}_quality_report.json")
        with open(qreport_path, "w", encoding="utf-8") as fh:
            import json

            json.dump({"dataset_id": dataset_id, "quality_score": quality_score, "details": details, "created_at": datetime.utcnow().isoformat()}, fh, indent=2)
        # persist report metadata
        try:
            conn = get_db_connection()
            conn.execute(
                "INSERT INTO reports (dataset_id, filename, path, format, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (dataset_id, os.path.basename(qreport_path), qreport_path, "quality_json", session.get("user_id"), datetime.utcnow().isoformat()),
            )
            conn.commit()
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception:
        pass
    return jsonify(analysis)


@app.route("/demo")
@login_or_jwt_required
def load_demo_dataset():
    demo_path = os.path.join(os.path.dirname(__file__), "sample_data.csv")
    data = pd.read_csv(demo_path)
    cleaned, quality_score, details = clean_dataset(data)
    dataset_id = "demo"
    analysis = prepare_analysis(cleaned, dataset_id, quality_score)
    analysis["quality_score"] = quality_score
    analysis["details"] = details
    DATASETS[dataset_id] = {"path": demo_path, "data": cleaned, "analysis": analysis}
    analysis["assistant_response"] = assistant_reply("Summarize the business performance", DATASETS[dataset_id])
    return jsonify(analysis)


@app.route("/assistant", methods=["POST"])
@login_or_jwt_required
def assistant_endpoint():
    payload = request.get_json(silent=True) or {}
    dataset_id = payload.get("dataset_id", "demo")
    message = payload.get("message", "")

    if dataset_id == "demo":
        demo_path = os.path.join(os.path.dirname(__file__), "sample_data.csv")
        if not os.path.exists(demo_path):
            return jsonify({"reply": "Upload or load a dataset first."})
        data = pd.read_csv(demo_path)
        cleaned, quality_score, details = clean_dataset(data)
        analysis = prepare_analysis(cleaned, "demo", quality_score)
        analysis["quality_score"] = quality_score
        analysis["details"] = details
        DATASETS[dataset_id] = {"path": demo_path, "data": cleaned, "analysis": analysis}

    dataset = DATASETS.get(dataset_id)
    if not dataset:
        return jsonify({"reply": "Upload or load a dataset first."})
    # Record user message (sanitize)
    user_id = session.get("user_id") or getattr(g, "user_id", None)
    safe_message = bleach.clean(message or "")
    reply = assistant_reply(safe_message, dataset)
    try:
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO chats (dataset_id, user_id, role, message, response, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (dataset_id, user_id, 'user', safe_message, bleach.clean(reply), datetime.utcnow().isoformat()),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return jsonify({"reply": reply})


@app.route("/export-report/<dataset_id>")
@login_or_jwt_required
def export_report(dataset_id):
    dataset = DATASETS.get(dataset_id)
    if not dataset:
        return jsonify({"error": "Dataset not found."}), 404

    report_path = os.path.join(REPORT_FOLDER, f"{dataset_id}_executive_report.xlsx")
    writer = pd.ExcelWriter(report_path, engine="openpyxl")
    summary_df = pd.DataFrame([
        {"Metric": "Dataset", "Value": dataset["analysis"]["dataset_name"]},
        {"Metric": "Rows", "Value": dataset["analysis"]["row_count"]},
        {"Metric": "Columns", "Value": dataset["analysis"]["column_count"]},
        {"Metric": "Quality Score", "Value": dataset["analysis"]["quality_score"]},
        {"Metric": "Metric Total", "Value": dataset["analysis"]["metric_total"]},
        {"Metric": "Growth Rate", "Value": f"{dataset['analysis']['growth_rate']}%"},
    ])
    summary_df.to_excel(writer, sheet_name="Executive Summary", index=False)
    dataset["data"].to_excel(writer, sheet_name="Cleaned Data", index=False)
    writer.close()
    # persist report record
    try:
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO reports (dataset_id, filename, path, format, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (dataset_id, os.path.basename(report_path), report_path, "xlsx", session.get("user_id"), datetime.utcnow().isoformat()),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return send_file(report_path, as_attachment=True)


@app.route("/export-pdf/<dataset_id>")
@login_or_jwt_required
def export_pdf(dataset_id):
    dataset = DATASETS.get(dataset_id)
    if not dataset:
        return jsonify({"error": "Dataset not found."}), 404

    pdf_path = os.path.join(REPORT_FOLDER, f"{dataset_id}_executive_report.pdf")
    packet = io.BytesIO()
    canvas_obj = canvas.Canvas(packet, pagesize=letter)
    canvas_obj.setTitle("InsightAI Executive Report")
    canvas_obj.setFont("Helvetica-Bold", 16)
    canvas_obj.drawString(40, 760, f"InsightAI Executive Report - {dataset['analysis']['dataset_name']}")
    canvas_obj.setFont("Helvetica", 11)
    lines = [
        f"Rows: {dataset['analysis']['row_count']}",
        f"Columns: {dataset['analysis']['column_count']}",
        f"Quality Score: {dataset['analysis']['quality_score']}",
        f"Metric Total: {dataset['analysis']['metric_total']}",
        f"Growth Rate: {dataset['analysis']['growth_rate']}%",
        "Recommendations:",
    ]
    y_position = 730
    for line in lines:
        canvas_obj.drawString(40, y_position, line)
        y_position -= 18
    for recommendation in dataset["analysis"].get("recommendations", []):
        canvas_obj.drawString(60, y_position, f"- {recommendation}")
        y_position -= 16
    canvas_obj.save()
    packet.seek(0)
    with open(pdf_path, "wb") as handle:
        handle.write(packet.getvalue())

    # persist report record
    try:
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO reports (dataset_id, filename, path, format, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (dataset_id, os.path.basename(pdf_path), pdf_path, "pdf", session.get("user_id"), datetime.utcnow().isoformat()),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return send_file(pdf_path, as_attachment=True)


@app.route('/api/aggregations')
@login_or_jwt_required
def api_aggregations():
    dataset_id = request.args.get('dataset_id', 'demo')
    year = request.args.get('year', 'all')
    category = request.args.get('category', 'all')
    dataset = DATASETS.get(dataset_id)
    if not dataset:
        return jsonify({'error': 'Dataset not found.'}), 404

    analysis = dataset.get('analysis', {})
    # If we have persisted aggregations, prefer SQL queries
    try:
        conn = get_db_connection()
        params = [dataset_id]
        sql = "SELECT period, metric_sum, category FROM dataset_aggregations WHERE dataset_id = ?"
        if year and year != 'all':
            sql += " AND period LIKE ?"
            params.append(f"%{year}%")
        if category and category != 'all':
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY period"
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        time_series = [{'label': r['period'], 'value': r['metric_sum']} for r in rows]
        top_categories = analysis.get('top_categories', [])
        return jsonify({'dataset_id': dataset_id, 'time_series': time_series, 'forecast': analysis.get('forecast', []), 'top_categories': top_categories})
    except Exception:
        # Fallback to in-memory time_series
        time_series = analysis.get('time_series', [])
        if year and year != 'all':
            time_series = [t for t in time_series if t.get('label', '').endswith(year)]
        top_categories = analysis.get('top_categories', [])
        return jsonify({'dataset_id': dataset_id, 'time_series': time_series, 'forecast': analysis.get('forecast', []), 'top_categories': top_categories})


@app.route('/auth/csrf', methods=['GET'])
def get_csrf_token():
    token = session.get('csrf_token')
    if not token:
        import secrets

        token = secrets.token_urlsafe(32)
        session['csrf_token'] = token
    return jsonify({'csrf_token': session['csrf_token']})


@app.before_request
def enforce_csrf():
    # Skip during testing
    if app.config.get('TESTING'):
        return
    if request.method in ('POST', 'PUT', 'DELETE'):
        # Skip CSRF enforcement for login/register/csrf/session endpoints
        if request.path.startswith('/auth/'):
            return
        # If JWT auth provided, skip CSRF
        auth = request.headers.get('Authorization')
        if auth and auth.startswith('Bearer '):
            return
        # Only enforce if session exists
        if 'user_id' in session:
            token = session.get('csrf_token')
            header = request.headers.get('X-CSRF-Token')
            if not token or not header or header != token:
                return jsonify({'error': 'CSRF token missing or invalid.'}), 403


@app.route('/admin/users')
@admin_required
def admin_list_users():
    conn = get_db_connection()
    rows = conn.execute('SELECT id, email, role, created_at FROM users ORDER BY id DESC').fetchall()
    conn.close()
    users = [{'id': r['id'], 'email': r['email'], 'role': r['role'] if 'role' in r.keys() else 'user'} for r in rows]
    return jsonify({'users': users})


@app.route('/admin/audit_logs')
@admin_required
@admin_rate_limit()
def admin_audit_logs():
    limit = int(request.args.get('limit', 200))
    offset = int(request.args.get('offset', 0))
    action = request.args.get('action')
    user_id = request.args.get('user_id')
    since = request.args.get('since')
    until = request.args.get('until')
    conn = get_db_connection()
    sql = 'SELECT id, user_id, action, target, details, created_at FROM audit_logs WHERE 1=1'
    params = []
    if action:
        sql += ' AND action = ?'
        params.append(action)
    if user_id:
        sql += ' AND user_id = ?'
        params.append(user_id)
    if since:
        sql += ' AND created_at >= ?'
        params.append(since)
    if until:
        sql += ' AND created_at <= ?'
        params.append(until)
    sql += ' ORDER BY id DESC LIMIT ? OFFSET ?'
    params.extend([limit, offset])
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    logs = [dict(r) for r in rows]
    return jsonify({'audit_logs': logs})


@app.route('/admin/datasets')
@admin_required
@admin_rate_limit()
def admin_datasets():
    conn = get_db_connection()
    rows = conn.execute('SELECT dataset_id, filename, path, row_count, column_count, quality_score, created_by, created_at FROM datasets ORDER BY created_at DESC').fetchall()
    conn.close()
    datasets = [dict(r) for r in rows]
    return jsonify({'datasets': datasets})


@app.route('/admin/promote/<int:user_id>', methods=['POST'])
@admin_required
def admin_promote_user(user_id):
    conn = get_db_connection()
    conn.execute('UPDATE users SET role = ? WHERE id = ?', ('admin', user_id))
    conn.commit()
    conn.close()
    # audit
    try:
        conn = get_db_connection()
        conn.execute('INSERT INTO audit_logs (user_id, action, target, details, created_at) VALUES (?, ?, ?, ?, ?)', (session.get('user_id'), 'promote', user_id, 'promoted to admin', datetime.utcnow().isoformat()))
        conn.commit()
        conn.close()
    except Exception:
        pass
    return jsonify({'message': 'User promoted to admin.'})


@app.route('/admin/demote/<int:user_id>', methods=['POST'])
@admin_required
def admin_demote_user(user_id):
    conn = get_db_connection()
    conn.execute('UPDATE users SET role = ? WHERE id = ?', ('user', user_id))
    conn.commit()
    conn.close()
    # audit
    try:
        conn = get_db_connection()
        conn.execute('INSERT INTO audit_logs (user_id, action, target, details, created_at) VALUES (?, ?, ?, ?, ?)', (session.get('user_id'), 'demote', user_id, 'demoted to user', datetime.utcnow().isoformat()))
        conn.commit()
        conn.close()
    except Exception:
        pass
    return jsonify({'message': 'User demoted to user.'})


@app.route('/admin/remove_user/<int:user_id>', methods=['POST'])
@admin_required
def admin_remove_user(user_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()
    # audit
    try:
        conn = get_db_connection()
        conn.execute('INSERT INTO audit_logs (user_id, action, target, details, created_at) VALUES (?, ?, ?, ?, ?)', (session.get('user_id'), 'remove_user', user_id, 'removed user', datetime.utcnow().isoformat()))
        conn.commit()
        conn.close()
    except Exception:
        pass
    return jsonify({'message': 'User removed.'})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8000)
