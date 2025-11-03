#!/usr/bin/env python3
"""
FabCon Global Hack 2025 - RBAC Sync Test Script
Quick validation of Snowflake and Fabric connectivity

Run this BEFORE running the full rbac_sync_automation.py script
to ensure your configuration is correct.

Author: Tyler Rabiger
"""

import os
import sys
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

print("=" * 80)
print("RBAC SYNC - CONFIGURATION TEST")
print("=" * 80)
print()

# Check if required packages are installed
print("📦 Checking dependencies...")
required_packages = ['snowflake', 'requests']
optional_packages = ['pandas']
missing_packages = []

for package in required_packages:
    try:
        __import__(package.replace('-', '_'))
        print(f"   ✅ {package}")
    except ImportError:
        print(f"   ❌ {package} - NOT INSTALLED")
        missing_packages.append(package)

for package in optional_packages:
    try:
        __import__(package)
        print(f"   ✅ {package} (optional)")
    except ImportError:
        print(f"   ⚠️  {package} (optional) - not installed, will use built-in csv")

if missing_packages:
    print(f"\n⚠️  Install missing packages: pip install -r requirements_rbac.txt")
    sys.exit(1)

print()

# Test Snowflake configuration
print("🔍 Testing Snowflake configuration...")
snowflake_config = {
    'account': os.getenv('SNOWFLAKE_ACCOUNT'),
    'user': os.getenv('SNOWFLAKE_USER'),
    'password': os.getenv('SNOWFLAKE_PASSWORD'),
    'warehouse': os.getenv('SNOWFLAKE_WAREHOUSE')
}

config_valid = True
for key, value in snowflake_config.items():
    if not value or value.startswith('your-'):
        print(f"   ❌ {key.upper()}: NOT CONFIGURED")
        config_valid = False
    else:
        # Mask password for security
        display_value = '***' if key == 'password' else value
        print(f"   ✅ {key.upper()}: {display_value}")

if not config_valid:
    print("\n⚠️  Update your .env file with Snowflake credentials")
    print("   See config.env.template for required variables")
    sys.exit(1)

print()

# Test Snowflake connection
print("🔌 Testing Snowflake connection...")
try:
    import snowflake.connector
    
    conn = snowflake.connector.connect(
        account=snowflake_config['account'],
        user=snowflake_config['user'],
        password=snowflake_config['password'],
        warehouse=snowflake_config['warehouse']
    )
    
    cursor = conn.cursor()
    cursor.execute("SELECT CURRENT_ACCOUNT(), CURRENT_USER(), CURRENT_WAREHOUSE()")
    row = cursor.fetchone()
    
    print(f"   ✅ Connected successfully!")
    print(f"   Account: {row[0]}")
    print(f"   User: {row[1]}")
    print(f"   Warehouse: {row[2]}")
    
    # Test role access
    print("\n🔐 Testing role access...")
    test_roles = ['FINANCE_ADMIN', 'FINANCE_ANALYST', 'FINANCE_VIEWER', 'AP_MANAGER', 'BUDGET_ANALYST']
    accessible_roles = []
    
    for role in test_roles:
        try:
            cursor.execute(f"SHOW GRANTS TO ROLE {role}")
            grants = cursor.fetchall()
            print(f"   ✅ {role}: {len(grants)} grants")
            accessible_roles.append(role)
        except Exception as e:
            if '002003' in str(e):  # Role does not exist
                print(f"   ❌ {role}: Role does not exist")
            else:
                print(f"   ⚠️  {role}: Access denied or other error")
    
    conn.close()
    
    if len(accessible_roles) == 0:
        print("\n⚠️  No roles accessible. Ensure roles exist and you have SHOW GRANTS privilege.")
        sys.exit(1)
    elif len(accessible_roles) < len(test_roles):
        print(f"\n⚠️  Only {len(accessible_roles)}/{len(test_roles)} roles accessible.")
        print("   This is OK if you're testing with a subset of roles.")
    
except Exception as e:
    print(f"   ❌ Connection failed: {str(e)}")
    print("\n💡 Troubleshooting tips:")
    print("   - Verify account format includes region (e.g., 'abc12345.east-us-2.azure')")
    print("   - Check if warehouse is running: SHOW WAREHOUSES;")
    print("   - Test credentials using SnowSQL CLI first")
    sys.exit(1)

print()

# Test Fabric configuration
print("🔍 Testing Fabric configuration...")
fabric_config = {
    'workspace_id': os.getenv('FABRIC_WORKSPACE_ID'),
    'tenant_id': os.getenv('AZURE_TENANT_ID'),
    'client_id': os.getenv('AZURE_CLIENT_ID'),
    'client_secret': os.getenv('AZURE_CLIENT_SECRET')
}

config_valid = True
for key, value in fabric_config.items():
    if not value or value.startswith('your-'):
        print(f"   ❌ {key.upper()}: NOT CONFIGURED")
        config_valid = False
    else:
        # Mask secret for security
        display_value = '***' if key == 'client_secret' else value
        print(f"   ✅ {key.upper()}: {display_value}")

if not config_valid:
    print("\n⚠️  Update your .env file with Fabric/Azure credentials")
    print("   See RBAC_SYNC_USAGE_GUIDE.md for service principal setup")
    sys.exit(1)

print()

# Test Fabric authentication
print("🔌 Testing Fabric API authentication...")
try:
    import requests
    
    token_url = f"https://login.microsoftonline.com/{fabric_config['tenant_id']}/oauth2/v2.0/token"
    
    payload = {
        'grant_type': 'client_credentials',
        'client_id': fabric_config['client_id'],
        'client_secret': fabric_config['client_secret'],
        'scope': 'https://analysis.windows.net/powerbi/api/.default'
    }
    
    response = requests.post(token_url, data=payload)
    
    if response.status_code == 200:
        token_data = response.json()
        print(f"   ✅ Authentication successful!")
        print(f"   Token expires in: {token_data.get('expires_in', 'unknown')} seconds")
        
        # Test workspace access
        print("\n🏢 Testing workspace access...")
        access_token = token_data['access_token']
        workspace_url = f"https://api.powerbi.com/v1.0/myorg/groups/{fabric_config['workspace_id']}"
        
        headers = {'Authorization': f'Bearer {access_token}'}
        workspace_response = requests.get(workspace_url, headers=headers)
        
        if workspace_response.status_code == 200:
            workspace = workspace_response.json()
            print(f"   ✅ Workspace accessible: {workspace.get('name', 'Unknown')}")
            
            # Get current workspace users
            users_url = f"{workspace_url}/users"
            users_response = requests.get(users_url, headers=headers)
            
            if users_response.status_code == 200:
                users = users_response.json().get('value', [])
                print(f"   ✅ Current workspace users: {len(users)}")
                
                # Check if service principal has admin access
                service_principal_id = fabric_config['client_id']
                sp_user = next((u for u in users if u.get('identifier') == service_principal_id), None)
                
                if sp_user:
                    sp_role = sp_user.get('groupUserAccessRight', 'Unknown')
                    if sp_role == 'Admin':
                        print(f"   ✅ Service principal has Admin access")
                    else:
                        print(f"   ⚠️  Service principal has {sp_role} access (needs Admin)")
                else:
                    print(f"   ⚠️  Service principal not found in workspace users")
                    print("      Add service principal with Admin role in workspace settings")
            else:
                print(f"   ⚠️  Could not retrieve workspace users: {users_response.status_code}")
                
        elif workspace_response.status_code == 404:
            print(f"   ❌ Workspace not found: {fabric_config['workspace_id']}")
            print("      Check workspace ID in Fabric portal URL")
        elif workspace_response.status_code == 403:
            print(f"   ❌ Access denied to workspace")
            print("      Ensure service principal has been added to workspace with Admin role")
        else:
            print(f"   ❌ Workspace access failed: {workspace_response.status_code}")
            
    else:
        print(f"   ❌ Authentication failed: {response.status_code}")
        error_data = response.json()
        print(f"   Error: {error_data.get('error_description', 'Unknown error')}")
        print("\n💡 Troubleshooting tips:")
        print("   - Verify tenant ID is correct (Azure AD → Properties)")
        print("   - Check client ID and secret (Azure AD → App registrations)")
        print("   - Ensure service principal has Power BI API permissions")
        sys.exit(1)
        
except Exception as e:
    print(f"   ❌ Fabric API test failed: {str(e)}")
    sys.exit(1)

print()
print("=" * 80)
print("✅ ALL TESTS PASSED - Ready to run rbac_sync_automation.py")
print("=" * 80)
print()
print("Next steps:")
print("1. Run dry run: python rbac_sync_automation.py")
print("2. Review generated CSV files and logs")
print("3. If satisfied, edit script to run live sync (uncomment line at bottom)")
print()
