# Docker Cluster

Appliance OVA à déployer sur ESXi (6.5 et plus). Chaque VM fonctionne en nœud
individuel ou rejoint un cluster, le tout piloté depuis une interface de
management unique.

> État : première brique, l'image de base. L'interface de management, le
> cluster (Docker Swarm), l'IP virtuelle et le stockage partagé arrivent dans
> les étapes suivantes.

## Contenu de l'image

- Ubuntu Server 24.04 LTS, image cloud *minimal* (sans snap ni cloud-init).
- Docker Engine et le plugin Compose, depuis le dépôt officiel Docker.
- open-vm-tools.
- VM en matériel virtuel **vmx-13** (ESXi 6.5), 2 vCPU, 4 Go de RAM, carte
  réseau VMXNET3, contrôleur SCSI paravirtuel.
- Disque 1 : système (20 Go). Disque 2 : données (50 Go, vide), réservé au
  futur stockage partagé entre les nœuds.

## Déploiement sur ESXi

1. Dans le Host Client : *Créer/Enregistrer une VM* → *Déployer une machine
   virtuelle à partir d'un fichier OVF ou OVA*, puis choisir le fichier `.ova`.
2. Dans *Paramètres supplémentaires*, renseigner les propriétés :

   | Propriété | Exemple | Obligatoire |
   |---|---|---|
   | Hostname | `docker-node1` | oui |
   | IP address | `192.168.1.10` (ou `192.168.1.10/24`) | oui |
   | Network prefix | `24` | oui |
   | Gateway | `192.168.1.1` | oui |
   | DNS servers | `192.168.1.1, 1.1.1.1` | conseillé |
   | Domain, NTP servers | | non |
   | Admin password | 8 caractères minimum | conseillé |
   | Console keyboard layout | `fr` | non |
   | Enable SSH | non par défaut | non |

3. Démarrer la VM. Au premier démarrage, elle applique ces réglages (réseau
   statique, nom, mot de passe du compte `admin`, SSH) puis affiche son
   adresse sur la console.

### Si les propriétés n'apparaissent pas

Certaines versions du Host Client 6.5 n'affichent pas les propriétés OVF ou ne
les transmettent pas à la VM. Dans ce cas la VM démarre en DHCP et la console
de la VM (écran ESXi) propose un assistant : clavier, nom, IP, passerelle, DNS,
puis mot de passe admin. Aucune ligne de commande n'est nécessaire.

Si l'import d'un gros `.ova` échoue dans le Host Client, extraire l'archive
(`tar -xf docker-cluster-<version>.ova`) et déployer avec les fichiers `.ovf`
et `.vmdk`.

## Console de la VM

L'écran de la VM (tty1) affiche l'état du nœud (nom, adresse, passerelle, SSH)
et, après saisie du mot de passe admin, un menu : réglages réseau, mot de passe,
activation de SSH, redémarrage, arrêt. Une session classique reste disponible
sur tty2 (Alt+F2).

## Construire l'OVA

Le build tourne dans GitHub Actions (`.github/workflows/build-ova.yml`) à chaque
push et PR. L'OVA est disponible dans les artefacts du run, et attaché à la
release quand on pousse un tag `v*`.

En local (Linux avec KVM) :

```sh
sudo apt install qemu-system-x86 qemu-utils xorriso   # + Packer >= 1.11
make ova VERSION=0.1.0          # output/docker-cluster-0.1.0.ova
make test                       # tests de la logique de premier démarrage
```

Sans KVM, ajouter `ACCEL=tcg` (beaucoup plus lent).

## Organisation du dépôt

```
packer/            build de l'image (Packer + QEMU) et scripts de provisionnement
rootfs/            fichiers installés tels quels dans l'appliance
  usr/local/lib/dc-appliance/appliance.py   premier démarrage + console de la VM
ova/               descripteur OVF (propriétés, matériel vmx-13) et empaquetage OVA
tests/             tests unitaires
```
