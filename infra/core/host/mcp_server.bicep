targetScope = 'resourceGroup'

@description('Location for the Container App')
param location string = resourceGroup().location

@description('Tags applied to all resources')
param tags object = {}

@description('Container App name')
param appName string

@description('Container Apps managed environment resource ID')
param managedEnvironmentId string

@description('Container Registry login server (e.g. myacr.azurecr.io)')
param registryLoginServer string

@description('Initial container image. Replace with the real image once it has been pushed to ACR.')
param image string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('Comma-separated list of allowed domains for the web_search tool. Empty = no restriction.')
param allowedDomains string = ''

@description('Application Insights connection string (optional)')
param appInsightsConnectionString string = ''

@description('Target port the container listens on')
param targetPort int = 80

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: managedEnvironmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: targetPort
        transport: 'auto'
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      // Registry auth is intentionally NOT set here. The AcrPull role
      // assignment below is created in the same deployment and AAD
      // propagation can race the container app's first pull. The
      // `deploy_mcp_server.py` script attaches ACR via
      // `az containerapp registry set` once the role is in place, then
      // updates the image to the freshly-built MCP server build.
    }
    template: {
      containers: [
        {
          name: 'mcp-server'
          image: image
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: concat(
            [
              {
                name: 'ALLOWED_DOMAINS'
                value: allowedDomains
              }
              {
                name: 'PORT'
                value: string(targetPort)
              }
            ],
            empty(appInsightsConnectionString) ? [] : [
              {
                name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
                value: appInsightsConnectionString
              }
            ]
          )
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

// Grant the container app's MI AcrPull on the registry so it can pull the image.
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: split(registryLoginServer, '.')[0]
}

resource acrPullRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, app.id, 'AcrPull')
  properties: {
    principalId: app.identity.principalId
    principalType: 'ServicePrincipal'
    // AcrPull
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}

output name string = app.name
output fqdn string = app.properties.configuration.ingress.fqdn
output url string = 'https://${app.properties.configuration.ingress.fqdn}'
output principalId string = app.identity.principalId
