# Vigilante iPhone 16 Pro (Back Market → Telegram)

Cada 30 min, GitHub Actions mira el precio de las URLs de `config.json` y te avisa por Telegram si bajan del umbral.

## Puesta en marcha (10 min)

1. **Bot de Telegram**: crea uno con @BotFather (o reutiliza uno tuyo) y apunta el token. Escríbele algo y saca tu chat_id en
   `https://api.telegram.org/bot<TOKEN>/getUpdates`.
2. **Repo en GitHub** (privado vale): sube todos estos archivos, incluida la carpeta `.github/workflows`.
3. **Secrets**: Settings → Secrets and variables → Actions → New repository secret:
   - `TELEGRAM_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. **Settings → Actions → General → Workflow permissions** → "Read and write permissions" (para guardar `estado.json`).
5. Lanza una vez a mano: Actions → "Vigilar iPhone 16 Pro" → Run workflow.

## Las URLs y la configuración

`config.json` ya trae los 4 colores en **256 GB · Excelente (`?l=10`) · SIM física + eSIM**, comprobados en la web.

- `umbral`: precio máximo (700).
- `bateria_nueva`: `true` compara el precio **con batería nueva** (lo lee de la propia ficha); `false` compara el precio estándar.
- Si Back Market muestra otro estado porque Excelente se agota, el bot no avisa de ese precio.

## Qué te va a llegar

- 🔥 Aviso cuando un color baja de 700 € (y otro si baja 5 € más).
- ↗️ Aviso si vuelve a subir.
- 📋 Un resumen diario a partir de las 9:00 → si un día no llega, el bot está caído.
- ⚠️ Un único aviso si Back Market empieza a bloquearlo.

## Probar en local

```bash
pip install -r requirements.txt
TELEGRAM_TOKEN=xxx TELEGRAM_CHAT_ID=yyy python vigilar.py --prueba
```
