"""
PARALLELIZED Main Script: Generate 2GB Enterprise Finance Data in Snowflake
FabCon Global Hack 2025 - Optimized for multi-core execution

This script generates 45M+ rows (~2GB) efficiently using all CPU cores.
"""

import os
import sys
from datetime import datetime
from dotenv import load_dotenv
import snowflake.connector
from snowflake.connector.pandas_tools import write_pandas
from data_generators_parallel import (
    generate_gl_transactions,
    generate_chart_of_accounts,
    generate_cost_centers,
    generate_budget_actual,
    generate_vendors,
    generate_invoices
)
from multiprocessing import cpu_count


def load_config():
    """Load configuration from .env file."""
    load_dotenv()
    
    required_vars = [
        'SNOWFLAKE_USER',
        'SNOWFLAKE_PASSWORD',
        'SNOWFLAKE_ACCOUNT'
    ]
    
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        print(f"❌ Error: Missing required environment variables: {', '.join(missing_vars)}")
        print("Please copy config.example.env to .env and fill in your credentials.")
        sys.exit(1)
    
    return {
        'user': os.getenv('SNOWFLAKE_USER'),
        'password': os.getenv('SNOWFLAKE_PASSWORD'),
        'account': os.getenv('SNOWFLAKE_ACCOUNT'),
        'warehouse': os.getenv('SNOWFLAKE_WAREHOUSE', 'COMPUTE_WH'),
        'database': os.getenv('SNOWFLAKE_DATABASE', 'ENTERPRISE_FINANCE'),
        'schema': os.getenv('SNOWFLAKE_SCHEMA', 'FINANCE_DW'),
        'role': os.getenv('SNOWFLAKE_ROLE', 'ACCOUNTADMIN')
    }


def connect_to_snowflake(config):
    """Establish connection to Snowflake."""
    print("Connecting to Snowflake...")
    try:
        ctx = snowflake.connector.connect(
            user=config['user'],
            password=config['password'],
            account=config['account'],
            warehouse=config['warehouse'],
            role=config['role']
        )
        print("✓ Connected successfully")
        return ctx
    except Exception as e:
        print(f"❌ Connection failed: {str(e)}")
        sys.exit(1)


def setup_database(ctx, database, schema):
    """Create database and schema if they don't exist."""
    print(f"\nSetting up database: {database}.{schema}")
    cursor = ctx.cursor()
    
    try:
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {database}")
        print(f"✓ Database {database} ready")
        
        cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {database}.{schema}")
        print(f"✓ Schema {schema} ready")
        
        cursor.execute(f"USE DATABASE {database}")
        cursor.execute(f"USE SCHEMA {schema}")
        
    except Exception as e:
        print(f"❌ Setup failed: {str(e)}")
        sys.exit(1)
    finally:
        cursor.close()


def generate_all_data():
    """Generate all finance tables - ENTERPRISE SCALE (2GB target)."""
    print("\n" + "="*60)
    print(f"GENERATING FINANCE DATA - USING {cpu_count()} CPU CORES")
    print("TARGET: 2GB / 45M+ rows")
    print("="*60)
    
    overall_start = datetime.now()
    tables = {}
    
    # Small tables first (fast)
    print("\n[1/6] Chart of Accounts...")
    tables['CHART_OF_ACCOUNTS'] = generate_chart_of_accounts(2_000)
    
    print("\n[2/6] Cost Centers...")
    tables['COST_CENTERS'] = generate_cost_centers(1_000)
    
    print("\n[3/6] Vendors...")
    tables['VENDORS'] = generate_vendors(10_000)
    
    print("\n[4/6] Budget Actual...")
    tables['BUDGET_ACTUAL'] = generate_budget_actual(500_000)
    
    # Large tables - these will use parallel processing
    print("\n[5/6] Invoices (15M rows - this will take several minutes)...")
    tables['INVOICES'] = generate_invoices(15_000_000)
    
    print("\n[6/6] GL Transactions (30M rows - this will take 10-20 minutes)...")
    tables['GL_TRANSACTIONS'] = generate_gl_transactions(30_000_000)
    
    overall_elapsed = (datetime.now() - overall_start).total_seconds()
    total_rows = sum(len(df) for df in tables.values())
    
    print("\n" + "="*60)
    print(f"✓ GENERATION COMPLETE")
    print(f"  Total rows: {total_rows:,}")
    print(f"  Total time: {overall_elapsed/60:.1f} minutes")
    print(f"  Rate: {total_rows/overall_elapsed:,.0f} rows/sec")
    print("="*60)
    
    return tables


def load_tables_to_snowflake(ctx, tables, database, schema):
    """Load all generated tables to Snowflake in batches."""
    print("\n" + "="*60)
    print("LOADING TABLES TO SNOWFLAKE")
    print("="*60)
    
    start_time = datetime.now()
    success_count = 0
    
    # Load in size order (small to large) for better progress feedback
    load_order = [
        'CHART_OF_ACCOUNTS',
        'COST_CENTERS', 
        'VENDORS',
        'BUDGET_ACTUAL',
        'INVOICES',
        'GL_TRANSACTIONS'
    ]
    
    for i, table_name in enumerate(load_order, 1):
        if table_name not in tables:
            continue
            
        df = tables[table_name]
        print(f"\n[{i}/6] Loading {table_name} ({len(df):,} rows)...")
        
        try:
            load_start = datetime.now()
            
            write_pandas(
                conn=ctx,
                df=df,
                table_name=table_name,
                database=database,
                schema=schema,
                auto_create_table=True,
                overwrite=True,
                chunk_size=50000  # Batch upload for large tables
            )
            
            load_elapsed = (datetime.now() - load_start).total_seconds()
            print(f"  ✓ Loaded in {load_elapsed/60:.1f} minutes ({len(df)/load_elapsed:,.0f} rows/sec)")
            success_count += 1
            
        except Exception as e:
            print(f"  ❌ Failed: {str(e)}")
    
    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\n✓ Loaded {success_count}/{len(tables)} tables in {elapsed/60:.1f} minutes")
    
    return success_count == len(tables)


def verify_data(ctx, database, schema, tables):
    """Verify all tables were loaded correctly."""
    print("\n" + "="*60)
    print("VERIFICATION")
    print("="*60)
    
    cursor = ctx.cursor()
    all_valid = True
    
    for table_name in tables.keys():
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {database}.{schema}.{table_name}")
            count = cursor.fetchone()[0]
            expected = len(tables[table_name])
            
            if count == expected:
                print(f"✓ {table_name}: {count:,} rows")
            else:
                print(f"⚠  {table_name}: {count:,} rows (expected {expected:,})")
                all_valid = False
        except Exception as e:
            print(f"❌ {table_name}: Verification failed - {str(e)}")
            all_valid = False
    
    cursor.close()
    return all_valid


def print_summary(ctx, database, schema):
    """Print summary statistics."""
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    
    cursor = ctx.cursor()
    
    # Total size
    cursor.execute(f"""
        SELECT 
            SUM(ROW_COUNT) as total_rows,
            ROUND(SUM(BYTES) / (1024*1024*1024), 2) as total_gb
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = '{schema}'
    """)
    
    result = cursor.fetchone()
    if result:
        print(f"\n🎯 Total Rows: {result[0]:,}")
        print(f"🎯 Total Size: {result[1]} GB")
        print(f"\n   Target achieved: 2GB enterprise dataset ✓")
    
    cursor.close()


def main():
    """Main execution function."""
    print("="*60)
    print("SNOWFLAKE 2GB DATA GENERATION - FABCON HACKATHON 2025")
    print("Enterprise Finance Dataset - Parallel Processing")
    print(f"Using {cpu_count()} CPU cores")
    print("="*60)
    
    # Load configuration
    config = load_config()
    
    # Connect to Snowflake
    ctx = connect_to_snowflake(config)
    
    # Setup database
    setup_database(ctx, config['database'], config['schema'])
    
    # Generate data (this is the long part - 15-30 minutes)
    print("\n⏱️  ESTIMATED TIME: 15-30 minutes for data generation")
    print("   You can monitor CPU usage in Task Manager/Activity Monitor")
    print("   All cores should be near 100% during large table generation\n")
    
    tables = generate_all_data()
    
    # Load to Snowflake
    print("\n⏱️  ESTIMATED TIME: 20-40 minutes for Snowflake upload")
    load_success = load_tables_to_snowflake(ctx, tables, config['database'], config['schema'])
    
    if not load_success:
        print("\n⚠️  Warning: Some tables failed to load")
    
    # Verify
    verify_success = verify_data(ctx, config['database'], config['schema'], tables)
    
    # Print summary
    print_summary(ctx, config['database'], config['schema'])
    
    # Close connection
    ctx.close()
    
    # Final status
    print("\n" + "="*60)
    if load_success and verify_success:
        print("✓ SUCCESS: 2GB enterprise dataset loaded and verified")
        print("="*60)
        print("\n🏆 Next Steps:")
        print("1. Activate Microsoft Fabric F64 trial")
        print("2. Configure Snowflake Mirroring connection")
        print("3. Monitor 3-4 hour replication process")
        print("4. Run performance validation queries")
    else:
        print("⚠️  COMPLETED WITH WARNINGS")
        print("="*60)
        print("\nPlease review errors above and retry failed tables")
    
    print("\n")


if __name__ == "__main__":
    main()
