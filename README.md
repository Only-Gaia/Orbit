# Orbit — Discord bot

Bot Discord in Python (discord.py 2.x) con moderazione, logs, inviti, livelli, giveaway, automod, economia e giochi.
Ogni comando funziona sia con il prefisso `.` sia come slash command `/`. Risposte e nomi dei comandi sono in inglese.

## File

| File | Contenuto |
|---|---|
| `main.py` | Avvio del bot e gestione errori |
| `config.py` | Impostazioni (prefisso, colori, limiti automod, ID owner) |
| `database.py` | Database SQLite (`orbit.db`, creato da solo al primo avvio) |
| `utils.py` | Funzioni condivise |
| `moderation.py` | ban, unban, kick, warn, unwarn, warns, nick, pex, depex, mute, unmute, purge, slowmode, lock, unlock |
| `logs.py` | logs set / disable e tutti gli eventi loggati |
| `invites.py` | invites, inviteboard |
| `levels.py` | level, leaderboard, rank, messages, reset invites / allinvites / messages / allmessages |
| `giveaways.py` | giveaway create / cancel / end, ticket premio, reroll automatico |
| `automod.py` | antispam, antilink, linkallow, antinuke, antiraid, blacklist |
| `extras.py` | embed, welcome, goodbye, stick, unstick, desk, quest, staffquest, userinfo, serverinfo, invitebot, help |
| `economy.py` | cash, work, fish, hunt, blackjack, roulette, coinflip, give, add, remove |
| `games.py` | ship, slap, kiss, fakekick, kill, hug, aura, gay, 8ball, say, marry, divorce, fakeban |

## Come metterlo online

1. **Discord Developer Portal** → New Application → *Bot* → copia il **token**.
2. Sempre in *Bot*, attiva **Server Members Intent** e **Message Content Intent**.
3. *OAuth2 → URL Generator*: spunta `bot` e `applications.commands`, permesso **Administrator**, poi apri il link per invitare il bot.
4. Nel server, porta il ruolo del bot **più in alto possibile** nella lista dei ruoli (serve per ban, kick, mute, pex...).
5. Metti il token in una variabile chiamata `TOKEN` (su Replit: *Secrets*). **Non scrivere mai il token nel codice e non caricarlo su GitHub.**
6. Installa le dipendenze: `pip install -r requirements.txt`
7. Avvia: `python main.py`

## Note

- Gli slash command possono impiegare qualche minuto ad apparire la prima volta.
- Il bot vede gli inviti e gli audit log solo con i permessi *Manage Server* e *View Audit Log*.
- Non caricare `orbit.db` su GitHub: contiene i dati del bot.
- Per cambiare chi può usare `add`, `remove` e `blacklist add/remove`, modifica `OWNER_IDS` in `config.py`.
