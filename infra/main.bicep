targetScope = 'subscription'
// targetScope = 'resourceGroup'

@minLength(1)
@maxLength(64)
@description('Name of the environment that can be used as part of naming resource convention')
param environmentName string

@minLength(1)
@maxLength(90)
@description('Name of the resource group to use or create')
param resourceGroupName string = 'rg-${environmentName}'

// Restricted locations to match list from
// https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/responses?tabs=python-key#region-availability
@minLength(1)
@description('Primary location for all resources')
@allowed([
  'australiaeast'
  'brazilsouth'
  'canadacentral'
  'canadaeast'
  'eastus'
  'eastus2'
  'francecentral'
  'germanywestcentral'
  'italynorth'
  'japaneast'
  'koreacentral'
  'northcentralus'
  'norwayeast'
  'polandcentral'
  'southafricanorth'
  'southcentralus'
  'southeastasia'
  'southindia'
  'spaincentral'
  'swedencentral'
  'switzerlandnorth'
  'uaenorth'
  'uksouth'
  'westus'
  'westus2'
  'westus3'
])
param location string

@metadata({azd: {
  type: 'location'
  usageName: [
    'OpenAI.GlobalStandard.gpt-4o-mini,10'
  ]}
})
param aiDeploymentsLocation string

@description('Id of the user or app to assign application roles')
param principalId string

@description('Principal type of user or app')
param principalType string

@description('Optional. Name of an existing AI Services account within the resource group. If not provided, a new one will be created.')
param aiFoundryResourceName string = ''

@description('Optional. Name of the AI Foundry project. If not provided, a default name will be used.')
param aiFoundryProjectName string = 'ai-project-${environmentName}'

@description('List of model deployments')
param aiProjectDeploymentsJson string = '[{"name":"gpt-4.1-mini","model":{"name":"gpt-4.1-mini","format":"OpenAI","version":"2025-04-14"},"sku":{"name":"GlobalStandard","capacity":10}}]'

@description('List of connections')
param aiProjectConnectionsJson string = '[]'

@description('List of resources to create and connect to the AI project')
param aiProjectDependentResourcesJson string = '[]'

var aiProjectDeployments = json(aiProjectDeploymentsJson)
var aiProjectConnections = json(aiProjectConnectionsJson)
var aiProjectDependentResources = json(aiProjectDependentResourcesJson)

@description('Enable hosted agent deployment')
param enableHostedAgents bool

@description('Enable monitoring for the AI project')
param enableMonitoring bool = true

@description('Comma-separated list of allowed domains for the web-search MCP server. Empty = no restriction.')
param mcpAllowedDomains string = 'wikipedia.org,lonelyplanet.com,tripadvisor.com,booking.com,kayak.com,skyscanner.com'

// Tags that should be applied to all resources.
// 
// Note that 'azd-service-name' tags should be applied separately to service host resources.
// Example usage:
//   tags: union(tags, { 'azd-service-name': <service name in azure.yaml> })
var tags = {
  'azd-env-name': environmentName
}

// Check if resource group exists and create it if it doesn't
resource rg 'Microsoft.Resources/resourceGroups@2021-04-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

// Build dependent resources array conditionally
// Check if ACR already exists in the user-provided array to avoid duplicates
var hasAcr = contains(map(aiProjectDependentResources, r => r.resource), 'registry')
var dependentResources = (enableHostedAgents) && !hasAcr ? union(aiProjectDependentResources, [
  {
    resource: 'registry'
    connectionName: 'acr-connection'
  }
]) : aiProjectDependentResources

// AI Project module
module aiProject 'core/ai/ai-project.bicep' = {
  scope: rg
  name: 'ai-project'
  params: {
    tags: tags
    location: aiDeploymentsLocation
    aiFoundryProjectName: aiFoundryProjectName
    principalId: principalId
    principalType: principalType
    existingAiAccountName: aiFoundryResourceName
    deployments: aiProjectDeployments
    connections: aiProjectConnections
    additionalDependentResources: dependentResources
    enableMonitoring: enableMonitoring
    enableHostedAgents: enableHostedAgents
  }
}

// Container Apps environment for the custom MCP server.
module mcpEnv 'core/host/container_app_env.bicep' = {
  scope: rg
  name: 'mcp-env'
  params: {
    location: location
    tags: tags
    environmentName: 'cae-${environmentName}'
  }
}

// Web-search MCP server (Container App). The image is initially a placeholder;
// `deploy_agents.py` builds & pushes the real image and updates the app.
module mcpServer 'core/host/mcp_server.bicep' = {
  scope: rg
  name: 'mcp-server'
  params: {
    location: location
    tags: tags
    appName: 'ca-mcp-${environmentName}'
    managedEnvironmentId: mcpEnv.outputs.id
    registryLoginServer: aiProject.outputs.dependentResources.registry.loginServer
    allowedDomains: mcpAllowedDomains
    appInsightsConnectionString: aiProject.outputs.APPLICATIONINSIGHTS_CONNECTION_STRING
  }
}

// Resources
//output AZURE_RESOURCE_GROUP string = resourceGroupName
//output AZURE_AI_ACCOUNT_ID string = aiProject.outputs.accountId
output AZURE_AI_PROJECT_ID string = aiProject.outputs.projectId
//output AZURE_AI_FOUNDRY_PROJECT_ID string = aiProject.outputs.projectId
//output AZURE_AI_ACCOUNT_NAME string = aiProject.outputs.aiServicesAccountName
//output AZURE_AI_PROJECT_NAME string = aiProject.outputs.projectName

// Endpoints
output AZURE_AI_PROJECT_ENDPOINT string = aiProject.outputs.AZURE_AI_PROJECT_ENDPOINT
output AZURE_OPENAI_ENDPOINT string = aiProject.outputs.AZURE_OPENAI_ENDPOINT
output APPLICATIONINSIGHTS_CONNECTION_STRING string = aiProject.outputs.APPLICATIONINSIGHTS_CONNECTION_STRING

// Dependent Resources and Connections

// ACR
//output AZURE_AI_PROJECT_ACR_CONNECTION_NAME string = aiProject.outputs.dependentResources.registry.connectionName
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = aiProject.outputs.dependentResources.registry.loginServer

// Custom MCP web-search server (Container App)
output MCP_SERVER_NAME string = mcpServer.outputs.name
output MCP_SERVER_URL string = '${mcpServer.outputs.url}/mcp/'
output MCP_SERVER_APP_NAME string = mcpServer.outputs.name

// Azure AI Search
//output AZURE_AI_SEARCH_CONNECTION_NAME string = aiProject.outputs.dependentResources.search.connectionName
//output AZURE_AI_SEARCH_SERVICE_NAME string = aiProject.outputs.dependentResources.search.serviceName

// Azure Storage
//output AZURE_STORAGE_CONNECTION_NAME string = aiProject.outputs.dependentResources.storage.connectionName
//output AZURE_STORAGE_ACCOUNT_NAME string = aiProject.outputs.dependentResources.storage.accountName

output AZURE_AI_MODEL_DEPLOYMENT_NAME string = 'gpt-4.1-mini'
output AZURE_OPENAI_CHAT_DEPLOYMENT_NAME string = 'gpt-4.1-mini'
output OPENAI_API_VERSION string = '2024-05-01-preview'

