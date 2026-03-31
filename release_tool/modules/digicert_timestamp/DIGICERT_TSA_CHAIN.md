# DigiCert RFC 3161 Timestamping — Chaîne de confiance

## Contexte

Un TSR (Timestamp Response) RFC 3161 prouve que :
- Un fichier existait à un instant précis
- Il n'a pas changé d'un seul bit depuis

Le serveur DigiCert utilisé : `http://timestamp.digicert.com`

---

## La hiérarchie complète

```
DigiCert Assured ID Root CA                        [Root CA — expire 2031]
    │   Auto-signé, présent dans tous les OS
    │
    └── DigiCert Trusted Root G4                   [Root intermédiaire — ~2038]
              │   Signé par Assured ID Root CA
              │   Présent dans /etc/ssl/certs
              │
              └── DigiCert Trusted G4 TimeStamping RSA4096 SHA256 2025 CA1   [~15 mois]
                            │   Signé par Trusted Root G4
                            │   Embarqué dans le TSR
                            │
                            └── DigiCert SHA256 RSA4096 Timestamp Responder 2025 1   [~15 mois]
                                          │   Signé par CA1
                                          │   Embarqué dans le TSR
                                          │
                                          └── TON TSR (manifest-v2.0.22.json.tsr)
```

---

## Détail de chaque certificat

### DigiCert Assured ID Root CA
- **Rôle** : Racine de toute la confiance DigiCert
- **Créé** : 10 novembre 2006
- **Expire** : 10 novembre 2031
- **Auto-signé** : oui (issuer = subject)
- **Fingerprint SHA-256** : `3E:90:99:B5:01:5E:8F:48:6C:00:BC:EA:9D:11:1E:E7:21:FA:BA:35:5A:89:BC:F1:DF:69:56:1E:3D:C6:32:5C`
- **Présence** : pré-installé dans tous les OS, navigateurs, magasins de certificats
- **Change ?** : jamais — modifier ce certificat invaliderait toute la chaîne construite dessus
- **Sa clé privée** : utilisée une poignée de fois dans toute sa vie (signe uniquement Trusted Root G4 et quelques autres Root G4)
- **Transition 2031** : DigiCert a anticipé l'expiration via `Trusted Root G4` qui deviendra Root CA indépendant avant 2031

### DigiCert Trusted Root G4
- **Rôle** : Root intermédiaire — signe toutes les PKI DigiCert (timestamping, SSL, code signing...)
- **Créé** : 2022
- **Expire** : ~2038
- **Présence** : dans `/etc/ssl/certs`, pré-installé progressivement dans les OS
- **Change ?** : non, stable comme un Root CA
- **Relation avec Assured ID Root CA** : signé par lui (cross-signing), ce qui permet la transition avant 2031

### DigiCert Trusted G4 TimeStamping RSA4096 SHA256 2025 CA1
- **Rôle** : CA intermédiaire dédié au timestamping
- **Durée** : ~15 mois (obligation CA/Browser Forum)
- **Embarqué dans le TSR** : oui
- **Signe quoi** : uniquement les Responder (certificats qui signent les TSR)
- **Rotation** : remplacé par `2026 CA1` etc. à chaque cycle

### DigiCert SHA256 RSA4096 Timestamp Responder 2025 1
- **Rôle** : signe physiquement les TSR
- **Durée** : ~15 mois (obligation CA/Browser Forum)
- **Embarqué dans le TSR** : oui
- **Signe quoi** : des millions de TSR individuels
- **Rotation** : remplacé régulièrement (`2025 2`, `2026 1`, etc.)
- **Variantes** : SHA256, SHA384, SHA512 — selon l'algo de hash utilisé

---

## Ce qui change vs ce qui ne change pas

| Élément | Change ? | Fréquence | Où |
|---|---|---|---|
| `Assured ID Root CA` | Non | Jamais (expire 2031) | Système (`/etc/ssl/certs`) |
| `Trusted Root G4` | Non | Jamais (~expire 2038) | Système (`/etc/ssl/certs`) |
| `TimeStamping 2025 CA1` | Oui | ~15 mois | Embarqué dans le TSR |
| `Timestamp Responder 2025 1` | Oui | ~15 mois | Embarqué dans le TSR |
| Ton `.tsr` | Non | Figé à la création | Ton fichier |

---

## Pourquoi 15 mois pour les intermédiaires ?

Règle imposée par le **CA/Browser Forum** (consortium SSL/PKI) : les certificats de code signing et timestamping ont une durée de vie courte pour limiter l'exposition en cas de compromission de clé privée.

La logique : plus on descend dans la hiérarchie, plus la clé est utilisée (et donc exposée), donc plus elle doit tourner vite.

| Niveau | Utilisations de la clé privée |
|---|---|
| Root CA | ~10 fois dans toute sa vie |
| CA intermédiaire | ~quelques milliers |
| Responder | Des millions (1 par TSR) |

---

## Ce que contient ton fichier `.tsr`

Un TSR RFC 3161 est un token PKCS#7 qui embarque :
- La réponse horodatée (timestamp + hash du fichier)
- La signature cryptographique
- Les certificats de la chaîne (**sauf le Root CA**)

Les Root CA ne sont jamais embarqués dans les TSR — convention universelle, car ils sont supposés être déjà présents dans le système.

---

## Vérification future (dans 5, 10, 20 ans)

Le TSR est **auto-contenu** : il embarque les certs 2025 qui ont signé la réponse.

`openssl ts -verify` vérifie la signature **au moment où elle a été faite**, pas au moment de la vérification. Les certs 2025 expirés ne posent pas de problème.

**La seule dépendance externe** : `DigiCert Trusted Root G4` (ou `Assured ID Root CA`) doit être dans le système.

```
En 2035 :
  TSR contient les certs 2025 (expirés mais toujours présents dans le fichier)
    + Root G4 trouvé dans /etc/ssl/certs (toujours valide jusqu'en ~2038)
      → Vérification OK
```

Si le Root CA disparaît du système (cas extrême), solution : le fournir manuellement via `-CAfile`.

---

## Ce que fait verify_tsr.py

```
1. extract_chain()      → extrait les certs embarqués dans le TSR
2. print_chain_subjects() → affiche subject/issuer de chaque cert (informatif)
3. get_root_issuer()    → lit le dernier issuer= de la chaîne → c'est le Root CA à trouver
4. build_full_chain()   → cherche ce Root CA dans /etc/ssl/certs, concatène chain + Root CA
5. verify()             → openssl ts -verify avec la full chain
```

La `full_chain` finale = certs extraits du TSR + Root CA du système.

---

## Événements à surveiller

| Date | Événement | Impact |
|---|---|---|
| ~tous les 15 mois | Rotation Responder + CA1 | Aucun — les nouveaux certs sont dans les nouveaux TSR |
| Novembre 2031 | Expiration `Assured ID Root CA` | Aucun si `Trusted Root G4` est bien dans le système |
| ~2038 | Expiration `Trusted Root G4` | DigiCert publiera un successeur bien avant |

---

## Ce qui adviendra de `DigiCert Assured ID Root CA` après 2031

### Il ne disparaît pas instantanément

Les OS et navigateurs retirent les Root CA expirés **progressivement**, parfois des années après l'expiration, pour ne pas casser les anciens systèmes. Il restera probablement dans `/etc/ssl/certs` encore un moment après 2031.

### Les TSR signés avant 2031 restent valides

La signature a été faite quand le certificat était valide. `openssl ts -verify` accepte ça — c'est le principe même du timestamping long terme. Un TSR n'est pas invalidé par l'expiration ultérieure des certificats qui l'ont signé.

### `Trusted Root G4` prend le relais

Avant 2031, DigiCert migrera entièrement vers `Trusted Root G4` comme nouvelle ancre de confiance. Il est déjà présent dans les systèmes. La chaîne évoluera de :

```
Assured ID Root CA → Trusted Root G4 → CA1 → Responder
```

vers :

```
Trusted Root G4 (Root indépendant) → CA1 → Responder
```

### Le vrai risque résiduel

Si en 2032 :
- `Assured ID Root CA` est retiré du système **ET**
- `Trusted Root G4` n'est pas encore reconnu comme Root de confiance indépendant

alors `verify_tsr.py` échouerait à construire la full chain. Solution : fournir le Root CA manuellement via `-CAfile`.

**Recommandation** : conserver les fichiers `.pem` de la chaîne complète à côté de chaque `.tsr` pour pouvoir reconstruire la full chain sans dépendre du système.

---

## Le mécanisme de cross-signing

### Ce qu'est une signature de certificat

Signer un certificat = signer **{identité + clé publique}** avec sa propre clé privée.

`Trusted Root G4` possède :
- une clé privée (secrète, jamais partagée)
- une clé publique (diffusée dans les certificats)

### Ce qui s'est passé concrètement

**Étape 1** — DigiCert crée `Trusted Root G4` et se signe lui-même :
```
Trusted Root G4 signe {"CN=Trusted Root G4" + sa propre clé publique}
→ produit le certificat auto-signé (dans /etc/ssl/certs)
```

**Étape 2** — DigiCert demande à `Assured ID Root CA` de signer la même clé publique :
```
Assured ID Root CA signe {"CN=Trusted Root G4" + la clé publique de Trusted Root G4}
→ produit le certificat cross-signé (embarqué dans le TSR)
```

Les deux certificats contiennent **exactement la même clé publique**. Seul le signataire change.

### Il existe donc deux certificats `Trusted Root G4`

| Version | issuer | Où |
|---|---|---|
| Auto-signé | lui-même | `/etc/ssl/certs` — utilisé comme Root indépendant |
| Cross-signé | `Assured ID Root CA` | Embarqué dans le TSR |

### Pourquoi ce double certificat ?

Pour assurer la transition entre l'ancien et le nouveau Root CA :

```
Systèmes récents  → font confiance à Trusted Root G4 directement (auto-signé)
Systèmes anciens  → remontent via Assured ID Root CA → Trusted Root G4 cross-signé
```

### Ce que prouve le cross-signing

`AID signe {"CN=Trusted Root G4" + clé publique de TRG}` prouve :

> "AID atteste que cette clé publique appartient bien à DigiCert Trusted Root G4."

Ce lien cryptographique est **figé pour toujours** dans le certificat cross-signé. Il ne disparaît pas en 2031. Après l'expiration d'AID, on pourra toujours prouver que TRG lui appartenait bien.

### La chaîne de confiance en termes de signatures

```
AID       signe la clé publique de → TRG
TRG       signe la clé publique de → CA1 (TimeStamping 2025)
CA1       signe la clé publique de → Responder 2025
Responder signe                    → ton TSR
```

Chaque niveau certifie la clé publique du niveau suivant. Pour vérifier le TSR, on remonte la chaîne en vérifiant chaque signature avec la clé publique du niveau au-dessus.

---

## Archivage long terme et re-timestamping

### Le problème à horizon 50 ans

La cryptographie seule ne garantit pas la vérifiabilité au-delà de ~30 ans sans intervention humaine périodique, pour deux raisons :
- Les Root CA expirent et sont retirés des systèmes
- Les algorithmes crypto (SHA256, RSA4096) seront potentiellement cassés par l'informatique quantique (~2040+)

La solution est le **re-timestamping** : re-signer périodiquement le TSR précédent avec des certificats et algorithmes frais.

```
2025 : TSR signé avec Responder 2025 / SHA256 / RSA4096
2035 : on re-signe le TSR de 2025 avec les certs de 2035
2045 : on re-signe le bundle de 2035 avec les certs de 2045
...
```

Chaque couche prouve que la couche précédente existait et était intacte à ce moment-là.

### Les standards techniques

| Standard | Description |
|---|---|
| **RFC 4998 — ERS** (Evidence Record Syntax) | Définit comment empiler des TSR successifs pour l'archivage long terme |
| **ETSI EN 319 102 / PAdES LTV** | Standard européen pour signatures long terme, intègre le re-timestamping dans les PDF signés |

### Les services qui implémentent le re-timestamping

#### Institutionnel (le plus fiable à 50 ans)

| Service | Pays | Horizon | Public cible | Tarif |
|---|---|---|---|---|
| **CINES** | France | 50-100 ans | Recherche publique française | Gratuit/quasi-gratuit sur dossier (financé par le MESRI) |
| **BnF** | France | Indéfini | Éditeurs français | Obligation légale (dépôt légal) |
| **Österreichisches Staatsarchiv** | Autriche | 50+ ans | Institutions | Sur devis |

#### Commercial

| Service | Horizon | Re-timestamping | Tarif |
|---|---|---|---|
| **Preservica** | 50+ ans | Oui | Plusieurs milliers €/an, sur devis |
| **Arkivum** | 50+ ans | Oui | Sur devis, orienté recherche |
| **DocuSign LTV** | ~20 ans | Partiel | Sur devis entreprise |

#### Qualifié eIDAS (Europe)

Sous le règlement eIDAS, les **QTSP** (Qualified Trust Service Providers) agréés par les États membres proposent des horodatages qualifiés avec obligations légales de conservation. En France, la liste des QTSP agréés est publiée par l'**ANSSI**.

### Évolution des algorithmes crypto

| Horizon | Risque |
|---|---|
| ~2030 | SHA1 déjà cassé (retiré depuis 2017) |
| ~2035 | RSA2048 potentiellement vulnérable |
| ~2040+ | RSA4096 et SHA256 sous pression quantique |

Le NIST a standardisé en 2024 les premiers algorithmes **post-quantum** (ML-DSA). Les services sérieux devront migrer avant que RSA4096 soit compromis.

### Ce que ça implique pour ce projet

Pour un horizon 50 ans, la stratégie recommandée en ordre de priorité :

1. **TSR RFC 3161 actuel** — preuve immédiate solide (~15-30 ans sans intervention)
2. **Zenodo (CERN)** — pérennité institutionnelle, DOI pérenne, sauvegardes distribuées
3. **CINES** — si besoin légal fort, re-timestamping géré de manière transparente, option la plus sérieuse dans le contexte de la recherche publique française

> Les tarifs exacts de Preservica, Arkivum et des QTSP eIDAS sont sur devis et sujets à changement — consulter directement leurs sites pour des chiffres à jour.

---

## Alternatives à DigiCert — Quel TSA choisir ?

### Le problème avec DigiCert

DigiCert est une entreprise privée américaine. Risques potentiels :
- Rachat, faillite, changement de politique commerciale
- Soumise au droit américain (CLOUD Act)
- L'URL `http://timestamp.digicert.com` peut disparaître

**Important** : pour la vérification, ça ne change rien — le TSR est auto-contenu, le serveur DigiCert n'est plus nécessaire une fois le `.tsr` généré.

### Les alternatives RFC 3161 reconnues eIDAS

| Service | Type | Gratuit | Pays |
|---|---|---|---|
| **DigiCert** | Entreprise privée | Oui (usage limité) | USA |
| **Sectigo** | Entreprise privée | Non | USA |
| **Bundesdruckerei (D-Trust)** | Public allemand | Non | Allemagne |
| **Certinomis / CertEurope** | Agréé ANSSI | Non | France |
| **FreeTSA.org** | Associatif | Oui | — |

Pour une reconnaissance légale en Europe, le TSA doit être un **QTSP agréé eIDAS**. La liste française est publiée par l'ANSSI.

---

## OpenTimestamps — La preuve décentralisée via Bitcoin

### Principe

OpenTimestamps est un projet open source (créé par Peter Todd, contributeur Bitcoin Core) qui ancre les timestamps dans la **blockchain Bitcoin**.

Au lieu d'écrire une transaction par document, il agrège des milliers de documents en un **arbre de Merkle** et n'écrit qu'une seule transaction Bitcoin pour tous :

```
Document A ─┐
Document B ─┤→ Merkle root → 1 transaction Bitcoin (toutes les ~6h)
Document C ─┤
Document D ─┘
```

### Pourquoi c'est gratuit

Le coût d'une transaction Bitcoin (~$5-15 selon la congestion) est partagé entre des milliers d'utilisateurs. Peter Todd et les opérateurs de calendriers publics absorbent ce coût, considéré comme un bien commun pour l'écosystème Bitcoin.

**Coût estimé du service annuellement :**
```
4 transactions/jour × 365 jours × $5-15 = ~$7 000 à $22 000/an
```
Distribué entre plusieurs opérateurs de calendriers indépendants.

### Comment ça prouve ton document individuellement

Tu reçois un fichier `.ots` — une **preuve de Merkle** : le chemin mathématique reliant ton document au Merkle root inscrit dans Bitcoin.

```
hash(ton document) + chemin Merkle → Merkle root → transaction Bitcoin du bloc X
```

Vérifiable par n'importe qui avec un nœud Bitcoin, sans serveur tiers.

### Le filet de sécurité fondamental

Même si tous les serveurs OpenTimestamps fermaient demain, la preuve reste **dans Bitcoin**. Il suffit du fichier `.ots` + d'un nœud Bitcoin pour vérifier — pour toujours, tant que Bitcoin existe.

C'est fondamentalement différent de DigiCert : si le serveur OCSP de DigiCert disparaît, la vérification devient plus complexe. Avec OpenTimestamps, il n'y a pas de serveur central dont dépend la vérification.

### Limite

Il y a un délai de ~6 heures entre le dépôt et l'inscription dans Bitcoin (agrégation par batch). Ce n'est pas instantané comme un TSR DigiCert.

### Comparaison globale des approches

| Approche | Légal eIDAS | Décentralisé | Gratuit | Horizon | Re-timestamping |
|---|---|---|---|---|---|
| DigiCert RFC 3161 | Oui | Non | Oui | ~30 ans | Non |
| QTSP ANSSI | Oui | Non | Non | ~30 ans | Non |
| OpenTimestamps (Bitcoin) | Non | Oui | Oui | Tant que Bitcoin | N/A |
| CINES | Oui | Non | Oui (public) | 100 ans | Oui |
| Arweave | Non | Oui | One-shot | 200 ans (théorique) | N/A |

### Recommandation optimale

La combinaison **DigiCert + OpenTimestamps** sur le même fichier est probablement la plus robuste :
- TSR DigiCert → preuve légalement reconnue eIDAS
- OpenTimestamps → preuve décentralisée ancrée dans Bitcoin, indépendante de toute entreprise

Deux preuves indépendantes qui se renforcent mutuellement.
