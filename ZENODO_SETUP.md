# Configuration Zenodo

## Variables d'environnement requises

Ajoutez les variables suivantes à votre fichier `.env` :

```bash
# Nom de base pour les fichiers PDF
# Le PDF final sera nommé: {BASE_NAME}-{TAG_NAME}.pdf
BASE_NAME=mon-document

# Configuration Zenodo (optionnel - uniquement si vous voulez publier sur Zenodo)
# Token d'accès Zenodo : https://zenodo.org/account/settings/applications/tokens/new/
ZENODO_TOKEN=votre_token_ici

# DOI concept de votre enregistrement Zenodo existant (ex: 10.5281/zenodo.1234567)
# C'est le DOI qui ne change pas entre les versions
ZENODO_CONCEPT_DOI=10.5281/zenodo.XXXXXXX

# URL de l'API Zenodo (par défaut: https://zenodo.org/api)
# Utilisez https://sandbox.zenodo.org/api pour les tests
ZENODO_API_URL=https://zenodo.org/api
```

## Fonctionnement

Lors de la création d'une release :

1. **Vérification du tag** : Le système vérifie que le tag n'existe pas, ou s'il existe, qu'il pointe sur le dernier commit de la branche distante.

2. **Création de la release GitHub** : Une release GitHub est créée avec le tag spécifié.

3. **Renommage du PDF** : Le fichier `main.pdf` est copié et renommé en `{BASE_NAME}-{TAG_NAME}.pdf`.

4. **Publication sur Zenodo** (si configuré) :
   - Crée une nouvelle version de l'enregistrement Zenodo existant
   - Supprime les anciens fichiers de la version draft
   - Upload le nouveau PDF
   - Met à jour les métadonnées avec le numéro de version (tag)
   - Publie la nouvelle version

## Obtenir un token Zenodo

1. Connectez-vous à Zenodo : https://zenodo.org
2. Allez dans vos paramètres : https://zenodo.org/account/settings/applications/tokens/new/
3. Créez un nouveau token avec les permissions :
   - `deposit:actions`
   - `deposit:write`
4. Copiez le token dans votre fichier `.env`

## Trouver le DOI concept

Le DOI concept est le DOI qui reste constant pour toutes les versions d'un enregistrement.

Exemple : Si votre enregistrement a le DOI `10.5281/zenodo.1234568`, le DOI concept sera `10.5281/zenodo.1234567` (sans le dernier chiffre de version).

Vous pouvez le trouver :
1. Sur la page de votre enregistrement Zenodo
2. Dans la section "Cite as" → "Cite all versions"

## Test avec Zenodo Sandbox

Pour tester sans affecter votre enregistrement de production :

1. Créez un compte sur https://sandbox.zenodo.org
2. Créez un enregistrement de test
3. Dans `.env`, utilisez :
   ```bash
   ZENODO_API_URL=https://sandbox.zenodo.org/api
   ```

## Si Zenodo n'est pas configuré

Si les variables `ZENODO_TOKEN` ou `ZENODO_CONCEPT_DOI` ne sont pas définies dans `.env`, le script :
- Créera quand même la release GitHub
- Renommera le PDF
- Affichera un avertissement indiquant que la publication Zenodo a été ignorée
