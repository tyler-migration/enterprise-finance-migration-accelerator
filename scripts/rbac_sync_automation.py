#!/usr/bin/env python3
"""
FabCon Global Hack 2025 - RBAC Sync Automation
Enterprise Finance Migration Accelerator

Automates Snowflake RBAC → Microsoft Fabric workspace permission migration.
Demonstrates solving the documented Microsoft limitation: 
"Granular security must be reconfigured in Fabric"

Author: Tyler Rabiger
Date: October 2025
"""

import os
import json
import logging
from datetime import datetime
from typing import Dict, List, Tuple
import snowflake.connector
import requests
from dataclasses import dataclass
import pandas as pd

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f'rbac_sync_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


@dataclass
class SnowflakeGrant:
    """Represents a single Snowflake grant"""
    role: str
    privilege: str
    granted_on: str
    name: str
    granted_by: str


@dataclass
class FabricPermission:
    """Represents a Fabric workspace permission assignment"""
    email: str
    role: str  # Admin, Member, Contributor, Viewer
    snowflake_role: str
    grant_count: int
    reasoning: str


class SnowflakeRBACExporter:
    """Exports RBAC grants from Snowflake"""
    
    def __init__(self, account: str, user: str, password: str, warehouse: str):
        self.account = account
        self.user = user
        self.password = password
        self.warehouse = warehouse
        self.conn = None
        
    def connect(self) -> bool:
        """Establish Snowflake connection"""
        try:
            self.conn = snowflake.connector.connect(
                account=self.account,
                user=self.user,
                password=self.password,
                warehouse=self.warehouse
            )
            logger.info(f"✅ Connected to Snowflake account: {self.account}")
            return True
        except Exception as e:
            logger.error(f"❌ Snowflake connection failed: {str(e)}")
            return False
    
    def export_role_grants(self, role_name: str) -> List[SnowflakeGrant]:
        """Export all grants for a specific role"""
        if not self.conn:
            raise ConnectionError("Not connected to Snowflake")
        
        try:
            cursor = self.conn.cursor()
            cursor.execute(f"SHOW GRANTS TO ROLE {role_name}")
            
            grants = []
            for row in cursor.fetchall():
                # Snowflake SHOW GRANTS returns: created_on, privilege, granted_on, name, granted_to, grantee_name, grant_option, granted_by
                grant = SnowflakeGrant(
                    role=role_name,
                    privilege=row[1],
                    granted_on=row[2],
                    name=row[3],
                    granted_by=row[7] if len(row) > 7 else 'UNKNOWN'
                )
                grants.append(grant)
            
            logger.info(f"✅ Exported {len(grants)} grants for role: {role_name}")
            return grants
            
        except Exception as e:
            logger.error(f"❌ Failed to export grants for {role_name}: {str(e)}")
            return []
    
    def export_all_roles(self, roles: List[str]) -> Dict[str, List[SnowflakeGrant]]:
        """Export grants for multiple roles"""
        all_grants = {}
        for role in roles:
            all_grants[role] = self.export_role_grants(role)
        return all_grants
    
    def close(self):
        """Close Snowflake connection"""
        if self.conn:
            self.conn.close()
            logger.info("✅ Snowflake connection closed")


class FabricPermissionMapper:
    """Maps Snowflake RBAC to Fabric workspace permissions"""
    
    # Mapping rules based on RBAC_MAPPING_GUIDE.md
    ROLE_MAPPINGS = {
        'FINANCE_ADMIN': {
            'fabric_role': 'Admin',
            'reasoning': 'Full CRUD access across all tables + role management',
            'user_email': 'finance.director@company.com'
        },
        'FINANCE_ANALYST': {
            'fabric_role': 'Member',
            'reasoning': 'Broad SELECT access, needs to create reports/notebooks',
            'user_email': 'finance.analyst@company.com'
        },
        'FINANCE_VIEWER': {
            'fabric_role': 'Viewer',
            'reasoning': 'Read-only access to dashboards, no artifact creation',
            'user_email': 'cfo@company.com'
        },
        'AP_MANAGER': {
            'fabric_role': 'Contributor',
            'reasoning': 'INSERT/UPDATE on INVOICES and VENDORS, needs AP workflows',
            'user_email': 'ap.manager@company.com'
        },
        'BUDGET_ANALYST': {
            'fabric_role': 'Contributor',
            'reasoning': 'INSERT/UPDATE on BUDGET_ACTUAL, needs budget planning reports',
            'user_email': 'budget.analyst@company.com'
        }
    }
    
    @staticmethod
    def map_role(snowflake_role: str, grants: List[SnowflakeGrant]) -> FabricPermission:
        """Map a Snowflake role to Fabric permission"""
        
        if snowflake_role not in FabricPermissionMapper.ROLE_MAPPINGS:
            logger.warning(f"⚠️  No mapping defined for role: {snowflake_role}")
            return None
        
        mapping = FabricPermissionMapper.ROLE_MAPPINGS[snowflake_role]
        
        return FabricPermission(
            email=mapping['user_email'],
            role=mapping['fabric_role'],
            snowflake_role=snowflake_role,
            grant_count=len(grants),
            reasoning=mapping['reasoning']
        )
    
    @staticmethod
    def analyze_grants(grants: List[SnowflakeGrant]) -> Dict:
        """Analyze grants to understand permission scope"""
        analysis = {
            'total_grants': len(grants),
            'has_delete': False,
            'has_insert_update': False,
            'has_select_only': False,
            'table_privileges': {},
            'database_usage': False,
            'warehouse_usage': False
        }
        
        for grant in grants:
            # Check privilege types
            if grant.privilege == 'DELETE':
                analysis['has_delete'] = True
            elif grant.privilege in ['INSERT', 'UPDATE']:
                analysis['has_insert_update'] = True
            elif grant.privilege == 'SELECT':
                analysis['has_select_only'] = True
            
            # Track table-level privileges
            if grant.granted_on == 'TABLE':
                table_name = grant.name.split('.')[-1]  # Extract table name
                if table_name not in analysis['table_privileges']:
                    analysis['table_privileges'][table_name] = []
                analysis['table_privileges'][table_name].append(grant.privilege)
            
            # Track database/warehouse usage
            if grant.granted_on == 'DATABASE' and grant.privilege == 'USAGE':
                analysis['database_usage'] = True
            if grant.granted_on == 'WAREHOUSE' and grant.privilege == 'USAGE':
                analysis['warehouse_usage'] = True
        
        return analysis


class FabricWorkspaceSync:
    """Syncs permissions to Microsoft Fabric workspace via REST API"""
    
    def __init__(self, workspace_id: str, tenant_id: str, client_id: str, client_secret: str):
        self.workspace_id = workspace_id
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = None
    
    def authenticate(self) -> bool:
        """Get Azure AD access token for Fabric API"""
        try:
            token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
            
            payload = {
                'grant_type': 'client_credentials',
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'scope': 'https://analysis.windows.net/powerbi/api/.default'
            }
            
            response = requests.post(token_url, data=payload)
            response.raise_for_status()
            
            self.access_token = response.json()['access_token']
            logger.info("✅ Successfully authenticated with Fabric API")
            return True
            
        except Exception as e:
            logger.error(f"❌ Fabric API authentication failed: {str(e)}")
            return False
    
    def add_workspace_user(self, permission: FabricPermission, dry_run: bool = False) -> bool:
        """Add or update user permission in Fabric workspace"""
        
        if dry_run:
            logger.info(f"🔍 [DRY RUN] Would assign: {permission.email} → {permission.role}")
            return True
        
        if not self.access_token:
            logger.error("❌ Not authenticated. Call authenticate() first.")
            return False
        
        try:
            url = f"https://api.powerbi.com/v1.0/myorg/groups/{self.workspace_id}/users"
            
            headers = {
                'Authorization': f'Bearer {self.access_token}',
                'Content-Type': 'application/json'
            }
            
            payload = {
                'identifier': permission.email,
                'groupUserAccessRight': permission.role,
                'principalType': 'User'
            }
            
            response = requests.post(url, headers=headers, json=payload)
            
            if response.status_code == 200:
                logger.info(f"✅ Assigned {permission.email} as {permission.role} in workspace")
                return True
            else:
                logger.error(f"❌ Failed to assign permission: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error adding workspace user: {str(e)}")
            return False
    
    def get_workspace_users(self) -> List[Dict]:
        """Get current workspace users"""
        if not self.access_token:
            logger.error("❌ Not authenticated. Call authenticate() first.")
            return []
        
        try:
            url = f"https://api.powerbi.com/v1.0/myorg/groups/{self.workspace_id}/users"
            headers = {'Authorization': f'Bearer {self.access_token}'}
            
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            
            users = response.json().get('value', [])
            logger.info(f"✅ Retrieved {len(users)} workspace users")
            return users
            
        except Exception as e:
            logger.error(f"❌ Error getting workspace users: {str(e)}")
            return []


class RBACMigrationOrchestrator:
    """Orchestrates the complete RBAC migration process"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.exporter = None
        self.syncer = None
        self.results = {
            'exported_roles': {},
            'mapped_permissions': [],
            'sync_results': [],
            'errors': []
        }
    
    def run_export(self) -> bool:
        """Step 1: Export Snowflake RBAC"""
        logger.info("=" * 80)
        logger.info("STEP 1: EXPORTING SNOWFLAKE RBAC GRANTS")
        logger.info("=" * 80)
        
        try:
            self.exporter = SnowflakeRBACExporter(
                account=self.config['snowflake']['account'],
                user=self.config['snowflake']['user'],
                password=self.config['snowflake']['password'],
                warehouse=self.config['snowflake']['warehouse']
            )
            
            if not self.exporter.connect():
                return False
            
            roles = self.config['snowflake']['roles_to_export']
            self.results['exported_roles'] = self.exporter.export_all_roles(roles)
            
            # Save exports to CSV for documentation
            for role, grants in self.results['exported_roles'].items():
                df = pd.DataFrame([{
                    'role': g.role,
                    'privilege': g.privilege,
                    'granted_on': g.granted_on,
                    'name': g.name,
                    'granted_by': g.granted_by
                } for g in grants])
                
                filename = f"{role.lower()}_grants.csv"
                df.to_csv(filename, index=False)
                logger.info(f"📁 Saved export: {filename}")
            
            self.exporter.close()
            return True
            
        except Exception as e:
            logger.error(f"❌ Export failed: {str(e)}")
            self.results['errors'].append(f"Export error: {str(e)}")
            return False
    
    def run_mapping(self) -> bool:
        """Step 2: Map Snowflake roles to Fabric permissions"""
        logger.info("=" * 80)
        logger.info("STEP 2: MAPPING TO FABRIC PERMISSIONS")
        logger.info("=" * 80)
        
        try:
            for role, grants in self.results['exported_roles'].items():
                permission = FabricPermissionMapper.map_role(role, grants)
                
                if permission:
                    self.results['mapped_permissions'].append(permission)
                    
                    # Analyze grants for detailed logging
                    analysis = FabricPermissionMapper.analyze_grants(grants)
                    
                    logger.info(f"\n📋 {role} → Fabric {permission.role}")
                    logger.info(f"   Email: {permission.email}")
                    logger.info(f"   Grants: {analysis['total_grants']}")
                    logger.info(f"   Tables: {len(analysis['table_privileges'])}")
                    logger.info(f"   Reasoning: {permission.reasoning}")
            
            # Save mapping summary
            df = pd.DataFrame([{
                'snowflake_role': p.snowflake_role,
                'fabric_role': p.role,
                'user_email': p.email,
                'grant_count': p.grant_count,
                'reasoning': p.reasoning
            } for p in self.results['mapped_permissions']])
            
            df.to_csv('snowflake_fabric_role_mapping.csv', index=False)
            logger.info("\n📁 Saved mapping: snowflake_fabric_role_mapping.csv")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Mapping failed: {str(e)}")
            self.results['errors'].append(f"Mapping error: {str(e)}")
            return False
    
    def run_sync(self, dry_run: bool = False) -> bool:
        """Step 3: Sync permissions to Fabric workspace"""
        logger.info("=" * 80)
        logger.info(f"STEP 3: SYNCING TO FABRIC WORKSPACE {'(DRY RUN)' if dry_run else ''}")
        logger.info("=" * 80)
        
        try:
            self.syncer = FabricWorkspaceSync(
                workspace_id=self.config['fabric']['workspace_id'],
                tenant_id=self.config['fabric']['tenant_id'],
                client_id=self.config['fabric']['client_id'],
                client_secret=self.config['fabric']['client_secret']
            )
            
            if not dry_run and not self.syncer.authenticate():
                return False
            
            success_count = 0
            for permission in self.results['mapped_permissions']:
                result = self.syncer.add_workspace_user(permission, dry_run=dry_run)
                self.results['sync_results'].append({
                    'email': permission.email,
                    'role': permission.role,
                    'success': result
                })
                if result:
                    success_count += 1
            
            logger.info(f"\n✅ Successfully synced {success_count}/{len(self.results['mapped_permissions'])} permissions")
            return True
            
        except Exception as e:
            logger.error(f"❌ Sync failed: {str(e)}")
            self.results['errors'].append(f"Sync error: {str(e)}")
            return False
    
    def generate_report(self) -> str:
        """Generate migration report"""
        logger.info("=" * 80)
        logger.info("MIGRATION REPORT")
        logger.info("=" * 80)
        
        report = []
        report.append(f"\n📊 RBAC Migration Summary - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        report.append(f"Total Roles Processed: {len(self.results['exported_roles'])}")
        report.append(f"Total Grants Exported: {sum(len(g) for g in self.results['exported_roles'].values())}")
        report.append(f"Permissions Mapped: {len(self.results['mapped_permissions'])}")
        report.append(f"Sync Success Rate: {sum(r['success'] for r in self.results['sync_results'])}/{len(self.results['sync_results'])}")
        
        if self.results['errors']:
            report.append(f"\n⚠️  Errors Encountered: {len(self.results['errors'])}")
            for error in self.results['errors']:
                report.append(f"   - {error}")
        else:
            report.append("\n✅ No errors encountered")
        
        report.append("\n" + "=" * 80)
        
        report_text = "\n".join(report)
        logger.info(report_text)
        
        # Save to file
        with open(f'rbac_migration_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.txt', 'w') as f:
            f.write(report_text)
        
        return report_text
    
    def run_full_migration(self, dry_run: bool = False):
        """Execute complete migration workflow"""
        logger.info("\n" + "=" * 80)
        logger.info("STARTING RBAC MIGRATION WORKFLOW")
        logger.info("=" * 80 + "\n")
        
        # Step 1: Export
        if not self.run_export():
            logger.error("❌ Migration aborted: Export failed")
            return
        
        # Step 2: Map
        if not self.run_mapping():
            logger.error("❌ Migration aborted: Mapping failed")
            return
        
        # Step 3: Sync
        if not self.run_sync(dry_run=dry_run):
            logger.error("❌ Migration aborted: Sync failed")
            return
        
        # Generate report
        self.generate_report()
        
        logger.info("\n✅ RBAC MIGRATION COMPLETE\n")


def main():
    """Main execution function"""
    
    # Configuration
    # IMPORTANT: In production, use Azure Key Vault or environment variables
    config = {
        'snowflake': {
            'account': os.getenv('SNOWFLAKE_ACCOUNT', 'your-account'),  # e.g., 'abc12345.east-us-2.azure'
            'user': os.getenv('SNOWFLAKE_USER', 'TYLER_RABIGER'),
            'password': os.getenv('SNOWFLAKE_PASSWORD', 'your-password'),
            'warehouse': os.getenv('SNOWFLAKE_WAREHOUSE', 'COMPUTE_WH'),
            'roles_to_export': [
                'FINANCE_ADMIN',
                'FINANCE_ANALYST',
                'FINANCE_VIEWER',
                'AP_MANAGER',
                'BUDGET_ANALYST'
            ]
        },
        'fabric': {
            'workspace_id': os.getenv('FABRIC_WORKSPACE_ID', 'your-workspace-id'),
            'tenant_id': os.getenv('AZURE_TENANT_ID', 'your-tenant-id'),
            'client_id': os.getenv('AZURE_CLIENT_ID', 'your-client-id'),
            'client_secret': os.getenv('AZURE_CLIENT_SECRET', 'your-client-secret')
        }
    }
    
    # Create orchestrator
    orchestrator = RBACMigrationOrchestrator(config)
    
    # Run migration (dry run first to test)
    print("\n🔍 RUNNING DRY RUN (no actual permissions will be changed)\n")
    orchestrator.run_full_migration(dry_run=True)
    
    # Uncomment below to run actual sync
    # print("\n⚠️  RUNNING LIVE SYNC (permissions will be changed in Fabric)\n")
    # orchestrator.run_full_migration(dry_run=False)


if __name__ == "__main__":
    main()
