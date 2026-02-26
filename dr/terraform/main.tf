# Giant Eagle Receipt Lookup — Disaster Recovery Infrastructure
# Secondary region: Central US (primary: East US 2)
#
# DR Strategy:
#   - ADLS RA-GRS handles Delta data replication automatically
#   - This Terraform creates the secondary Databricks workspace (warm standby)
#   - On failover: activate workspace, spin up Lakebase, re-sync from Delta
#   - RPO: minutes (RA-GRS async lag)
#   - RTO: < 1 hour

terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.80"
    }
    databricks = {
      source  = "databricks/databricks"
      version = "~> 1.30"
    }
  }
}

variable "primary_region" {
  default = "eastus2"
}

variable "secondary_region" {
  default = "centralus"
}

variable "resource_group_name" {
  default = "rg-giant-eagle-dr"
}

# ─── Resource Group ───
resource "azurerm_resource_group" "dr" {
  name     = var.resource_group_name
  location = var.secondary_region

  tags = {
    environment = "dr-standby"
    project     = "giant-eagle-receipt-lookup"
  }
}

# ─── ADLS Gen2 (Secondary) ───
# Note: RA-GRS is configured on the PRIMARY storage account.
# This resource is for any DR-specific storage needs.
resource "azurerm_storage_account" "dr_storage" {
  name                     = "stgianteagledr"
  resource_group_name      = azurerm_resource_group.dr.name
  location                 = var.secondary_region
  account_tier             = "Standard"
  account_replication_type = "LRS"  # Secondary doesn't need GRS
  is_hns_enabled           = true    # ADLS Gen2

  tags = {
    environment = "dr-standby"
  }
}

# ─── Databricks Workspace (Warm Standby) ───
resource "azurerm_databricks_workspace" "dr_workspace" {
  name                = "dbw-giant-eagle-dr"
  resource_group_name = azurerm_resource_group.dr.name
  location            = var.secondary_region
  sku                 = "premium"

  # Use same managed resource group pattern as primary
  managed_resource_group_name = "rg-giant-eagle-dr-managed"

  tags = {
    environment = "dr-standby"
    failover    = "manual"
  }
}

# ─── VNet for Private Link (mirrors primary) ───
resource "azurerm_virtual_network" "dr_vnet" {
  name                = "vnet-giant-eagle-dr"
  resource_group_name = azurerm_resource_group.dr.name
  location            = var.secondary_region
  address_space       = ["10.1.0.0/16"]
}

resource "azurerm_subnet" "dr_private" {
  name                 = "snet-private"
  resource_group_name  = azurerm_resource_group.dr.name
  virtual_network_name = azurerm_virtual_network.dr_vnet.name
  address_prefixes     = ["10.1.1.0/24"]
}

# ─── Outputs ───
output "dr_workspace_url" {
  value = azurerm_databricks_workspace.dr_workspace.workspace_url
}

output "dr_workspace_id" {
  value = azurerm_databricks_workspace.dr_workspace.workspace_id
}

# ─── Failover Runbook (as comments) ───
# 
# FAILOVER PROCEDURE:
# 1. Verify primary region is truly down (not just a transient issue)
# 2. ADLS RA-GRS already has all Delta data in secondary region
# 3. Activate this standby workspace:
#    - terraform apply (if not already provisioned)
#    - Configure Unity Catalog metastore connection
# 4. Spin up new Lakebase instance in secondary region:
#    - Run infra/lakebase_setup.sql
#    - Configure Synced Tables from Delta Gold
# 5. Re-sync data:
#    - Synced Tables will auto-populate from Delta
#    - Run ai/embedding_pipeline.py to regenerate pgvector embeddings
#    - Native tables: reconcile from Delta (Zerobus captured same data)
# 6. Deploy Databricks App to secondary workspace
# 7. Update DNS / load balancer to point to secondary
# 8. Verify health check: GET /health
# 
# ESTIMATED RTO: 30-60 minutes
# ESTIMATED RPO: Minutes (RA-GRS async replication lag)
