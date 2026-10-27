# NotarialEase — instalação Ubuntu por curl

Entrada pública do instalador Python. Aplicação, release e imagem GHCR permanecem privadas. Este repositório não contém credenciais, backups ou dados de clientes.

## Instalar

No Ubuntu Server amd64 com curl, Python3 e sudo, execute (troque o IPv4 pelo endereço autorizado do seu servidor):

```bash
set -o pipefail
curl -fsSL https://raw.githubusercontent.com/duarteprg-eng/notarialease-installer/docker-bootstrap-v1.0.27-test.2/install.py | sudo python3 - --bind 127.0.0.1 --port 3080
```

Não é necessário baixar, copiar ou extrair ZIP/TAR. O código funciona via stdin. Para disponibilizar na VPN, informe o IPv4 da VPN em --bind.

O script reutiliza automaticamente github-release.token e ghcr-read.token em /root/.config/notarialease-install, pasta root0700 e arquivos root0600/0400. Se ausentes, solicita os dois acessos em prompts protegidos por /dev/tty e os guarda nesse diretório. Credenciais preexistentes conflitantes são preservadas. Nunca colocar tokens na URL ou nos argumentos do comando.

São dois acessos somente leitura: fine-grained Contents/Metadata apenas do repositório notarialease; PAT classic somente read:packages para GHCR. O classic alcança os pacotes acessíveis à conta emissora, sem permitir upload/exclusão. A preparação de autenticação é local e individual por servidor; não há token global no script público.

## Conferir acesso antes de instalar

```bash
set -o pipefail
curl -fsSL https://raw.githubusercontent.com/duarteprg-eng/notarialease-installer/docker-bootstrap-v1.0.27-test.2/install.py | sudo python3 - --check-access
```

Esse modo consulta a release privada e o manifesto da imagem por HTTPS, sem executar Docker, APT, setup ou banco.

## O que o modo de instalação faz

Reutiliza o instalador testado da release docker-v1.0.27-test.1 após conferir SHA256 fixado. Prepara Docker/Compose por fonte oficial se necessário, ativa os serviços, cria PostgreSQL e aplicativo em containers e valida a aplicação. Bases iniciais têm apenas os cadastros obrigatórios, sem atendimentos ou serventias.

O primeiro login exige troca da senha inicial definida pelo responsável. Projetos/volumes conflitantes são preservados e interrompem a instalação. Canal de teste: atualização automática permanece desativada.
