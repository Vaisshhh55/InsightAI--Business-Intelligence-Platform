# InsightAI Project Documentation

## Overview
InsightAI is a Flask-based business intelligence platform that processes uploaded datasets and delivers analytics, forecasting, reporting, and an AI assistant. This documentation provides a deeper technical reference beyond the main `README.md`.

## Architecture
### Components
- **Frontend**: Jinja2 templates in `templates/` and static assets in `static/`
- **Backend**: `app.py` handles Flask endpoints, authentication, data processing, assistant logic, and exports
- **Database**: SQLite database stored in `insightai.db`
- **Uploads**: File uploads persist in `uploads/`
- **Reports**: Generated analysis assets are saved to `reports/`

### Data Flow
1. User authenticates
2. User uploads a dataset
3. Backend cleans and validates the dataset
4. Dataset metadata and quality score are computed
5. Analytics and forecast data are prepared
6. AI assistant answers questions based on the dataset
7. Reports are generated and stored

## Core Files
### `app.py`
Contains all application logic, including:
- Flask route definitions
- Authentication and session management
- Dataset upload handling
- Data cleaning and analysis
- AI assistant intent handling
- Export and report generation
- Admin and audit utilities

### `requirements.txt`
Contains package dependencies required to run the app.

### `templates/`
Holds the HTML interface templates for the app.

### `static/`
Holds frontend JavaScript and CSS assets.

### `uploads/`
Stores uploaded user dataset files.

### `reports/`
Stores generated report files and quality summaries.

### `tests/`
Contains automated tests for the app.

## Data Cleaning and Analysis
### Data Cleaning
Data cleaning is performed by `clean_dataset(df)` in `app.py`.

Typical data cleaning steps:
- Normalize header names
- Drop duplicate or empty rows
- Convert numeric-like columns to numeric dtypes
- Parse date-like strings into datetime values
- Fill missing numeric values with column medians
- Fill or impute categorical values
- Smooth numeric outliers using a 3-sigma approach

### Data Quality Score
A dataset quality score is derived from:
- Missing value percentage
- Duplicate row count
- Column completeness

The score is used to help users understand the reliability of analytics output.

### Analysis Preparation
`prepare_analysis(cleaned_df, dataset_name, quality_score)` computes:
- Total sales/revenue
- Total profit (when available)
- Top categories/products
- Monthly trend statistics
- Forecast points for next-period predictions
- Executive summary text

## AI Assistant
### Intent Handling
The assistant is implemented in `assistant_reply(message, dataset)` and associated helper functions.

It follows this general flow:
1. Map dataset columns to canonical business concepts
2. Detect user intent from question text
3. Execute a dataset-specific analytics function
4. Build a structured response

### Supported Intents
The assistant supports the following broad categories:
- Sales
- Profit
- Customer
- Product
- Forecasting
- Recommendations
- Regional analysis
- Dataset summary
- Quality checks
- Executive summary

### Column Mapping
The assistant uses fuzzy matching to align dataset columns to canonical terms, such as:
- `sales`, `revenue`, `amount`
- `profit`, `margin`
- `order_date`, `sale_date`
- `product`, `item`, `sku`
- `customer`, `client`
- `region`, `state`, `territory`

### Response Structure
Assistant replies include:
- `answer`
- `stats`
- `explanation`
- `confidence`
- `recommendation` (when requested)

### Fallback Behavior
If the message cannot be mapped or the dataset lacks required fields, the assistant returns a polite fallback response and guidance on supported questions.

## Forecasting Model
### Approach
- Monthly aggregations from a date field
- Linear regression using `scikit-learn`

### Limitations
- Linear only; no seasonality modeling
- Requires enough history for meaningful forecasts
- Best used for short-term trend projection

### Output
The forecast engine outputs:\n- aggregated history table
- next-period forecast value
- basic trend narrative

## Security
### Passwords and Authentication
- Uses `werkzeug.security.generate_password_hash`
- Verifies with `check_password_hash`
- Supports regular login sessions
- Supports JWT for API clients

### Session Settings
- `SESSION_COOKIE_HTTPONLY` enabled
- `SESSION_COOKIE_SAMESITE` set
- CSRF tokens used for state-changing requests

### Input Hardening
- Sanitizes user inputs with `bleach`
- Validates file uploads by extension

### Deployment Notes
- In production, environment variables must be set:
  - `FLASK_SECRET_KEY`
  - `JWT_SECRET_KEY`
  - `ENV=production`
- Production mode enforces more secure cookie and header behavior

## Deployment
### Local Development
1. Create and activate a virtual environment
2. Install dependencies
3. Run `python app.py`
4. Open `http://127.0.0.1:8000`

### Production Considerations
- Use a production-ready server (e.g. Gunicorn)
- Secure environment variables
- Configure HTTPS and reverse proxy
- Consider migrating SQLite to MySQL/PostgreSQL for scale

## Testing
- Tests are in `tests/test_app.py`
- Run using `pytest`

## Enhancement Roadmap
- Semantic NLP and embeddings for better assistant understanding
- Dataset versioning and diff history
- Advanced forecasting models
- Multi-dataset comparisons
- Role-based dashboards and RBAC
- Docker and cloud deployment
- More robust dataset schema validation
- Data lineage tracking

## Notes on Current Scope and Constraints
- The AI assistant is dataset-aware and rule-based, not an external LLM.
- The project currently uses SQLite for persistence.
- Forecasting is intentionally lightweight to keep the app responsive.

## Helpful Commands
- `python app.py` — start the app
- `pytest` — run tests
- `pip install -r requirements.txt` — install dependencies

## Contact
For questions or follow-up improvements, review `app.py` and the assistant helper functions documented in this file.
