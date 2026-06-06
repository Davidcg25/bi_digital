# ui_runner_v4.py — Runner con datepicker y TZ
# Requiere: pip install customtkinter tkcalendar
import os, threading, subprocess, sys
from datetime import datetime, timedelta

import customtkinter as ctk
from tkcalendar import DateEntry

def _load_dotenv_fallback():
    path = os.path.join(os.getcwd(), '.env')
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                k = k.strip(); v = v.strip().strip('"').strip("'")
                os.environ.setdefault(k, v)

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    _load_dotenv_fallback()

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

ctk.set_appearance_mode('dark')
ctk.set_default_color_theme('blue')

BASE_URL_PRESETS = {
    'Global /rest/V1/ (prod)': 'https://converse.cl/rest/V1/',
    'Converse PE storeview (prod)': 'https://converse.cl/rest/converse_pe_store_view/V1/',
    'Converse CL storeview (prod)': 'https://converse.cl/rest/converse_cl_store_view/V1/',
    'Personalizado…': None,
}

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title('Magento Orders ETL v4 — Runner')
        self.geometry('980x820')

        for i in range(4):
            self.grid_columnconfigure(i, weight=1)
        self.grid_rowconfigure(12, weight=1)

        r = 0
        ctk.CTkLabel(self, text='Modo de ejecución').grid(row=r, column=0, sticky='w', padx=12, pady=(12,4))
        self.mode = ctk.CTkOptionMenu(self, values=['Extraer (API → NDJSON)', 'Transformar (NDJSON → Excel/SQL)', 'Full (extraer + transformar)'])
        self.mode.set('Extraer (API → NDJSON)')
        self.mode.grid(row=r, column=1, columnspan=3, sticky='ew', padx=12, pady=(12,4))

        r += 1
        ctk.CTkLabel(self, text='Campo de fecha').grid(row=r, column=0, sticky='w', padx=12, pady=4)
        self.date_field = ctk.CTkOptionMenu(self, values=['created_at (ventas creadas)', 'updated_at (incremental)'])
        self.date_field.set('created_at (ventas creadas)')
        self.date_field.grid(row=r, column=1, sticky='ew', padx=12, pady=4)

        ctk.CTkLabel(self, text='Zona horaria local').grid(row=r, column=2, sticky='w', padx=12, pady=4)
        self.tz = ctk.CTkEntry(self, placeholder_text='America/Lima')
        self.tz.insert(0, 'America/Lima')
        self.tz.grid(row=r, column=3, sticky='ew', padx=12, pady=4)

        r += 1
        ctk.CTkLabel(self, text='Rango de fechas').grid(row=r, column=0, sticky='w', padx=12, pady=4)
        self.date_mode = ctk.StringVar(value='days')
        self.rb_days   = ctk.CTkRadioButton(self, text='X días atrás (local)', variable=self.date_mode, value='days', command=self._toggle_date_mode)
        self.rb_range  = ctk.CTkRadioButton(self, text='Rango custom (local)', variable=self.date_mode, value='range', command=self._toggle_date_mode)
        self.rb_days.grid(row=r, column=1, sticky='w', padx=12, pady=4)
        self.rb_range.grid(row=r, column=2, sticky='w', padx=12, pady=4)

        r += 1
        ctk.CTkLabel(self, text='Días hacia atrás').grid(row=r, column=0, sticky='w', padx=12, pady=4)
        self.days = ctk.CTkEntry(self, placeholder_text='Ej: 2')
        self.days.insert(0, '2')
        self.days.grid(row=r, column=1, sticky='ew', padx=12, pady=4)

        r += 1
        ctk.CTkLabel(self, text='Desde (local)').grid(row=r, column=0, sticky='w', padx=12, pady=4)
        self.date_from_cal = DateEntry(self, date_pattern='yyyy-mm-dd')
        self.date_from_cal.grid(row=r, column=1, sticky='w', padx=12, pady=4)

        ctk.CTkLabel(self, text='Hasta (local)').grid(row=r, column=2, sticky='w', padx=12, pady=4)
        self.date_to_cal = DateEntry(self, date_pattern='yyyy-mm-dd')
        self.date_to_cal.grid(row=r, column=3, sticky='w', padx=12, pady=4)

        r += 1
        self.utc_preview = ctk.CTkLabel(self, text='UTC: —')
        self.utc_preview.grid(row=r, column=0, columnspan=4, sticky='w', padx=12, pady=(0,8))

        r += 1
        ctk.CTkLabel(self, text='Selector de BASE_URL').grid(row=r, column=0, sticky='w', padx=12, pady=4)
        self.base_choice = ctk.CTkOptionMenu(self, values=list(BASE_URL_PRESETS.keys()), command=self._on_base_choice)
        self.base_choice.set('Global /rest/V1/ (prod)')
        self.base_choice.grid(row=r, column=1, sticky='ew', padx=12, pady=4)

        r += 1
        ctk.CTkLabel(self, text='BASE_URL efectiva').grid(row=r, column=0, sticky='w', padx=12, pady=4)
        self.baseurl = ctk.CTkEntry(self, placeholder_text='https://…/rest/V1/ o …/store_view/V1/')
        self.baseurl.grid(row=r, column=1, columnspan=3, sticky='ew', padx=12, pady=4)

        r += 1
        ctk.CTkLabel(self, text='Carpeta de salida').grid(row=r, column=0, sticky='w', padx=12, pady=4)
        self.out = ctk.CTkEntry(self, placeholder_text='OUTPUT_FOLDER (Excel/NDJSON)')
        self.out.insert(0, os.path.abspath('.'))
        self.out.grid(row=r, column=1, columnspan=3, sticky='ew', padx=12, pady=4)

        r += 1
        ctk.CTkLabel(self, text='Modo de filas del reporte').grid(row=r, column=0, sticky='w', padx=12, pady=6)
        self.row_mode = ctk.CTkOptionMenu(self, values=['por_simple','por_configurable'])
        self.row_mode.set('por_simple')
        self.row_mode.grid(row=r, column=1, sticky='w', padx=12, pady=6)

        ctk.CTkLabel(self, text='Limitar status_histories a últimos…').grid(row=r, column=2, sticky='e', padx=12, pady=6)
        self.sh_limit = ctk.CTkOptionMenu(self, values=['0','3','5','10'])
        self.sh_limit.set('5')
        self.sh_limit.grid(row=r, column=3, sticky='w', padx=12, pady=6)

        r += 1
        ctk.CTkLabel(self, text='Log de ejecución').grid(row=r, column=0, sticky='w', padx=12, pady=(6,4))
        r += 1
        self.log = ctk.CTkTextbox(self, width=940, height=360)
        self.log.grid(row=r, column=0, columnspan=4, sticky='nsew', padx=12, pady=(0,6))

        r += 1
        self.btn = ctk.CTkButton(self, text='Ejecutar', command=self.run, height=44)
        self.btn.grid(row=r, column=0, columnspan=4, sticky='ew', padx=12, pady=12)

        self._toggle_date_mode()
        self._on_base_choice(self.base_choice.get())
        self._hook_events()

    def _hook_events(self):
        self.date_from_cal.bind('<<DateEntrySelected>>', lambda e: self._update_utc_preview())
        self.date_to_cal.bind('<<DateEntrySelected>>', lambda e: self._update_utc_preview())
        self.tz.bind('<FocusOut>', lambda e: self._update_utc_preview())
        self.date_mode.trace_add('write', lambda *a: self._update_utc_preview())

    def _toggle_date_mode(self):
        is_days = self.date_mode.get() == 'days'
        self.days.configure(state='normal' if is_days else 'disabled')
        state = 'normal' if not is_days else 'disabled'
        self.date_from_cal.configure(state=state)
        self.date_to_cal.configure(state=state)
        self.tz.configure(state=state)
        self._update_utc_preview()

    def _on_base_choice(self, choice):
        preset = BASE_URL_PRESETS.get(choice)
        if preset is None:
            self.baseurl.configure(state='normal')
            if not self.baseurl.get().strip():
                self.baseurl.insert(0, os.getenv('MAGENTO_BASE_URL',''))
        else:
            self.baseurl.configure(state='normal')
            self.baseurl.delete(0,'end')
            self.baseurl.insert(0, preset)
            self.baseurl.configure(state='disabled')

    def _local_to_utc_str(self, date_obj, end=False):
        hhmmss = '23:59:59' if end else '00:00:00'
        local_str = f"{date_obj.strftime('%Y-%m-%d')} {hhmmss}"
        tzname = self.tz.get().strip() or 'America/Lima'
        try:
            if ZoneInfo:
                loc = datetime.strptime(local_str, '%Y-%m-%d %H:%M:%S').replace(tzinfo=ZoneInfo(tzname))
                utc = loc.astimezone(ZoneInfo('UTC'))
                return utc.strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            pass
        offset = -5 if tzname.lower() == 'america/lima' else 0
        loc = datetime.strptime(local_str, '%Y-%m-%d %H:%M:%S')
        utc = loc + timedelta(hours=-offset)
        return utc.strftime('%Y-%m-%d %H:%M:%S')

    def _update_utc_preview(self):
        if self.date_mode.get() == 'range':
            d1 = self.date_from_cal.get_date(); d2 = self.date_to_cal.get_date()
            s = self._local_to_utc_str(datetime(d1.year, d1.month, d1.day), end=False)
            e = self._local_to_utc_str(datetime(d2.year, d2.month, d2.day), end=True)
            self.utc_preview.configure(text=f"UTC: {s}  →  {e}")
        else:
            self.utc_preview.configure(text='UTC: —')

    def _append_log(self, text):
        self.log.insert('end', text)
        self.log.see('end')
        self.update_idletasks()

    def run(self):
        def stream_cmd(cmd, env):
            p = subprocess.Popen([sys.executable] + cmd, env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 bufsize=1, universal_newlines=True)
            for line in p.stdout:
                self._append_log(line)
            return p.wait()

        def _worker():
            try:
                m = self.mode.get()
                env = os.environ.copy()

                env['DATE_FIELD'] = 'created_at' if self.date_field.get().startswith('created_at') else 'updated_at'
                env['LOCAL_TZ'] = self.tz.get().strip() or 'America/Lima'

                if self.date_mode.get() == 'days':
                    env['DAYS_BACK'] = self.days.get().strip() or '2'
                    env['DATE_FROM'] = ''
                    env['DATE_TO']   = ''
                else:
                    d1 = self.date_from_cal.get_date(); d2 = self.date_to_cal.get_date()
                    env['DATE_FROM'] = d1.strftime('%Y-%m-%d')
                    env['DATE_TO']   = d2.strftime('%Y-%m-%d')
                    env['DAYS_BACK'] = '0'

                bu = self.baseurl.get().strip()
                if bu:
                    env['MAGENTO_BASE_URL'] = bu

                out = self.out.get().strip()
                env['OUTPUT_FOLDER'] = out

                env['ROW_MODE'] = self.row_mode.get()
                env['STATUS_HISTORY_LIMIT'] = self.sh_limit.get()

                self._append_log(
                    f"\n[BOOT] Modo={m} | DATE_FIELD={env['DATE_FIELD']} | DATE_MODE={self.date_mode.get()} | "
                    f"DAYS_BACK={env.get('DAYS_BACK')} | DATE_FROM={env.get('DATE_FROM')} | DATE_TO={env.get('DATE_TO')} | "
                    f"TZ={env.get('LOCAL_TZ')} | SH_LIMIT={env['STATUS_HISTORY_LIMIT']} | ROW_MODE={env['ROW_MODE']} | "
                    f"OUTPUT_FOLDER={out} | BASE_URL={env.get('MAGENTO_BASE_URL','<default>')}\n"
                )

                rc = 0
                if 'Extraer' in m:
                    rc = stream_cmd(['extractor_magento.py'], env)
                elif 'Transformar' in m:
                    rc = stream_cmd(['transformer_report.py'], env)
                else:
                    rc = stream_cmd(['extractor_magento.py'], env)
                    if rc == 0:
                        rc = stream_cmd(['transformer_report.py'], env)

                if rc == 0:
                    self._append_log('\n[OK] Proceso finalizado.\n')
                else:
                    self._append_log(f'\n[ERROR] Proceso terminó con código {rc}.\n')
            except Exception as e:
                self._append_log(f"\n[ERROR] {e}\n")

        threading.Thread(target=_worker, daemon=True).start()

if __name__ == '__main__':
    App().mainloop()
