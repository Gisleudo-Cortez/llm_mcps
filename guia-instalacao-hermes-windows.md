# Guia de Instalação do Hermes Agent no Windows

**Um guia passo a passo para quem nunca usou terminal antes.**

---

## O que é o Hermes Agent?

O Hermes é um assistente de IA que roda no seu computador. Pense nele como um
ChatGPT que vive dentro do seu PC: ele pode criar arquivos, pesquisar na
internet, executar programas, organizar suas tarefas e muito mais. Ele funciona
por texto — você digita o que quer e ele responde.

É um projeto gratuito e de código aberto da Nous Research.

**Requisitos:** Windows 10 ou 11 (64 bits). Nada mais — o instalador cuida de tudo.

---

## Instalação (2 minutos)

### Passo 1: Abrir o PowerShell

O PowerShell é o terminal do Windows. É uma janela preta onde você digita
comandos. Não se assuste — você só vai precisar digitar UMA linha.

**Como abrir:**
1. Aperte a tecla `Windows` do teclado
2. Digite `PowerShell`
3. Clique em **Windows PowerShell** (ou **Terminal** no Windows 11)

Vai abrir uma janela azul ou preta com um cursor piscando. É ali que você vai
colar o comando.

![Aparência do PowerShell: janela com fundo azul e texto branco]()

### Passo 2: Colar e executar o instalador

Copie esta linha (selecione tudo e aperte `Ctrl+C`):

```
iex (irm https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.ps1)
```

**Como colar no PowerShell:**
- Clique com o botão **direito** do mouse dentro da janela do PowerShell
- O texto aparece colado automaticamente
- Aperte `Enter`

**Pronto.** Agora é só esperar. O instalador vai:
- Baixar e instalar tudo que o Hermes precisa (Python, Git, Node.js)
- Isso leva de 3 a 8 minutos dependendo da sua internet
- Você NÃO precisa clicar em nada durante a instalação
- O instalador mostra o progresso na tela

**Você não precisa de senha de administrador.** O instalador não pede permissões
especiais.

### Passo 3: Fechar e reabrir o PowerShell

Quando o instalador terminar, **feche o PowerShell** (clique no X) e abra de novo.

Isso é necessário para o Windows reconhecer o comando `hermes`.

### Passo 4: Verificar se funcionou

No novo PowerShell, digite:

```
hermes --version
```

Se aparecer um número de versão (ex: `0.14.0`), está tudo certo.

---

## Primeira conversa com o Hermes

### Configurar o modelo de IA

O Hermes precisa de um "cérebro" — um modelo de IA que vai gerar as respostas.
Existem duas opções:

#### Opção A: Portal da Nous (recomendado para iniciantes)

É uma assinatura paga que já inclui o modelo de IA + ferramentas extras (busca
na web, geração de imagens, etc). Um cadastro só, sem ficar copiando chaves.

No PowerShell, depois de instalado:

```
hermes setup --portal
```

Siga as instruções na tela para criar sua conta. Depois é só digitar `hermes`
e começar a conversar.

#### Opção B: OpenRouter (gratuito para testar, pago conforme usa)

O OpenRouter te dá acesso a vários modelos de IA. Você paga por uso — alguns
modelos são gratuitos.

**Passo a passo:**
1. Acesse https://openrouter.ai e crie uma conta gratuita
2. Vá em https://openrouter.ai/keys e clique em **Create Key**
3. Copie a chave gerada (começa com `sk-or-`)
4. No PowerShell, configure a chave:

```
hermes config set model.provider openrouter
hermes config set OPENROUTER_API_KEY sk-or-v1-seu-codigo-aqui
```

Substitua `sk-or-v1-seu-codigo-aqui` pela chave que você copiou.

5. Escolha um modelo gratuito para testar:

```
hermes config set model.default google/gemma-3-27b-it:free
```

### Iniciar uma conversa

No PowerShell, digite:

```
hermes
```

Vai aparecer uma tela de boas-vindas e um cursor onde você pode digitar.
Experimente:

```
Olá! Quem é você e o que pode fazer?
```

O Hermes vai responder. Para sair, digite `/quit` ou `/exit`.

---

## Comandos básicos

| Comando | O que faz |
|---------|-----------|
| `hermes` | Abre o chat interativo |
| `hermes model` | Trocar o modelo ou provedor de IA |
| `hermes doctor` | Verificar se está tudo funcionando |
| `/quit` ou `/exit` | Sair do chat |
| `/help` | Lista de comandos dentro do chat |
| `/new` | Começar conversa nova |
| `hermes config set` | Ajustar configurações |
| `hermes update` | Atualizar para a versão mais nova |

---

## O que dá pra fazer com o Hermes

- Conversar e tirar dúvidas
- Pesquisar na internet (precisa configurar uma chave de busca)
- Criar e editar arquivos no seu computador
- Executar programas e scripts
- Agendar tarefas automáticas
- Conectar com Telegram e Discord

**Tudo por texto, direto no terminal.**

---

## Problemas comuns e soluções

### "hermes: command not found" ou "O termo 'hermes' não é reconhecido"

**Causa:** O PowerShell antigo não reconhece o caminho novo.

**Solução:** Feche o PowerShell e abra um novo. Se ainda não funcionar, reinicie
o computador. O instalador adicionou o Hermes ao PATH do Windows, mas isso só
vale para janelas abertas depois da instalação.

### "API key not set" ou erro de chave

**Causa:** Você não configurou um provedor de IA.

**Solução:** Rode `hermes model` e siga as instruções, ou use
`hermes setup --portal` para configurar via conta Nous.

### Caracteres estranhos na tela (?????)

**Causa:** Terminal antigo sem suporte a caracteres especiais.

**Solução:** Use o **Windows Terminal** em vez do PowerShell antigo. O Windows
Terminal já vem instalado no Windows 11. No Windows 10, instale gratuitamente
pela Microsoft Store.

### O instalador travou ou deu erro

**Solução:** Feche o PowerShell, abra de novo e rode o mesmo comando. O
instalador é seguro para reexecutar — ele detecta o que já foi instalado.

Se continuar dando erro, acesse:
https://github.com/NousResearch/hermes-agent/issues

---

## Para saber mais

- **Site oficial:** https://hermes-agent.nousresearch.com/docs/
- **Guia completo do Windows:** https://hermes-agent.nousresearch.com/docs/user-guide/windows-native
- **GitHub:** https://github.com/NousResearch/hermes-agent
- **Comunidade:** Procure por "Nous Research" no Discord

---

## Desinstalar

Se quiser remover o Hermes completamente, abra o PowerShell e digite:

```
hermes uninstall
```

Isso remove o programa mas mantém suas conversas e configurações salvas. Para
apagar tudo:

```
Remove-Item -Recurse -Force "$env:USERPROFILE\.hermes"
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\hermes"
```

---

*Guia atualizado em Maio de 2026. Baseado na documentação oficial do Hermes Agent
v0.14.0 (early beta para Windows nativo).*

*Fontes:*
- https://hermes-agent.nousresearch.com/docs/getting-started/installation
- https://hermes-agent.nousresearch.com/docs/user-guide/windows-native
- https://github.com/NousResearch/hermes-agent
