# 📸 Captura IA

Capture qualquer área da tela e deixe a IA trabalhar: extrair o texto,
explicar o conteúdo ou traduzir — com o resultado já copiado, pronto pra colar.

Irmão do Ditado IA: mesma chave Groq, mesmo esquema de atualização pela nuvem.

## Como usar

1. Pressione **Alt+C** (ou clique no ícone da bandeja → "Capturar agora")
2. **Arraste** para selecionar a área da tela (Esc cancela)
3. Escolha a ação: **Extrair texto**, **Explicar** ou **Traduzir**
4. O resultado aparece numa janela e **já está copiado** — é só colar (Ctrl+V)

## Instalação

1. Extraia este zip numa pasta fixa (ex.: `C:\CapturaIA`) — nunca rode de dentro do zip
2. Se não tiver Python: https://www.python.org/downloads/ (marque **Add Python to PATH**)
3. Duplo clique em **`instalar.bat`**
4. Cole sua chave do Groq (a mesma do Ditado IA funciona): https://console.groq.com/keys

## Recursos

| Recurso | Como |
|---|---|
| Extrair texto de print/documento/foto | Ação "Extrair texto" |
| Explicar erro, gráfico ou documento | Ação "Explicar" |
| Traduzir texto na tela | Ação "Traduzir" |
| Ação padrão sem menu | Configurações → Ação padrão |
| Atalho configurável | Configurações → botão "Detectar" |
| Histórico (duplo clique copia) | Bandeja → Histórico |
| Sem internet? | Captura vai para "pendentes"; processe depois |
| Atualizações | Bandeja → Verificar atualizações (automático ao iniciar) |

## Onde ficam meus dados

- Configurações/histórico/log: `%APPDATA%\CapturaIA`
- A chave de API fica **somente no seu computador**
- As capturas são enviadas à Groq apenas para análise; nada é armazenado

## Problemas?

O log fica em `%APPDATA%\CapturaIA\app.log` — mande as últimas linhas pro Bob. 😄
