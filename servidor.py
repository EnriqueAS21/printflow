from waitress import serve
from app import app, HILOS_WAITRESS   # hilos: variable WAITRESS_HILOS (16 por defecto)

serve(app, host="0.0.0.0", port=5001, threads=HILOS_WAITRESS)
