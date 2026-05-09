targetScope = 'resourceGroup'

@description('Location for the Container Apps environment')
param location string = resourceGroup().location

@description('Tags applied to all resources')
param tags object = {}

@description('Container Apps environment name')
param environmentName string

@description('Log Analytics workspace resource ID for container app logs (optional)')
param logAnalyticsWorkspaceId string = ''

resource law 'Microsoft.OperationalInsights/workspaces@2022-10-01' existing = if (!empty(logAnalyticsWorkspaceId)) {
  name: last(split(logAnalyticsWorkspaceId, '/'))
}

resource cae 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: !empty(logAnalyticsWorkspaceId) ? {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: law.properties.customerId
        sharedKey: law.listKeys().primarySharedKey
      }
    } : null
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

output id string = cae.id
output name string = cae.name
