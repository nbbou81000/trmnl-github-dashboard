# Dashboard GitHub pour TRMNL OG (paysage, noir et blanc)

Un écran 800×480 en vrai 1-bit qui reprend tout un profil GitHub : contributions de l'année, séries, carte des 12 derniers mois, compteurs, top dépôts, langages, activité récente, dernières PR, répartition par jour, par heure et par mois, et topics.

Une fois par heure, GitHub Actions lit l'API GitHub, dessine la page, la photographie en PNG noir et blanc et la publie sur GitHub Pages. Le TRMNL affiche cette image.

## Mise en route (tout se fait en cliquant)

1. **Pages** : Settings → Pages → Source : **GitHub Actions**.
2. **Lancer** : onglet Actions → « Dashboard GitHub pour TRMNL » → **Run workflow**.
3. **Vérifier** : https://nbbou81000.github.io/trmnl-github-dashboard/ montre l'image.
4. **TRMNL** : crée un plugin privé.
   - Strategy : **Polling**
   - Polling URL : `https://nbbou81000.github.io/trmnl-github-dashboard/data.json`
   - Remove bleed margin : **Yes**
   - Markup « Full » : colle le contenu de `trmnl-markup.html`

## Options

- **Afficher un autre compte** : Settings → Secrets and variables → Actions → onglet *Variables* → `GH_LOGIN` = le pseudo voulu.
- **Si le workflow échoue avec « Resource not accessible »**, ou pour compter aussi tes contributions privées : crée un jeton (Settings du profil → Developer settings → Personal access tokens → Tokens (classic), case `read:user`), puis ajoute-le en *Secret* nommé `GH_STATS_TOKEN`.
