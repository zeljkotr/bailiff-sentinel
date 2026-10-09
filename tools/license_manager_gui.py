import base64
import json
import sys
import uuid
import tkinter as tk
from datetime import date, timedelta
from pathlib import Path
from tkinter import ttk, messagebox
from cryptography.hazmat.primitives import serialization

BASE_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
PRIVATE_KEY_FILE = BASE_DIR / "private_key.pem"
LOG_FILE = BASE_DIR / "issued_licenses.log"


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class LicenseManager:
    def __init__(self, root):
        self.root = root
        root.title("Bailiff Sentinel License Manager")
        root.geometry("680x510")
        root.minsize(600, 460)

        main = ttk.Frame(root, padding=18)
        main.pack(fill="both", expand=True)

        ttk.Label(
            main, text="Bailiff Sentinel License Manager",
            font=("Segoe UI", 16, "bold")
        ).pack(anchor="w", pady=(0, 16))

        ttk.Label(main, text="Korisnik / naziv firme").pack(anchor="w")
        self.licensee = ttk.Entry(main)
        self.licensee.pack(fill="x", pady=(4, 12))

        ttk.Label(main, text="Trajanje licence").pack(anchor="w")
        self.duration = tk.StringVar(value="365")
        self.duration_box = ttk.Combobox(
            main, textvariable=self.duration, state="readonly",
            values=("30", "90", "180", "365", "730", "Do datuma", "Trajna")
        )
        self.duration_box.pack(fill="x", pady=(4, 8))
        self.duration_box.bind("<<ComboboxSelected>>", self.update_expiry)

        self.expiry = ttk.Entry(main)
        self.expiry.insert(0, date.today().replace(year=date.today().year + 1).isoformat())
        self.expiry.pack(fill="x", pady=(0, 12))
        self.expiry_label = ttk.Label(main, text="Datum isteka (YYYY-MM-DD), za izbor „Do datuma“")
        self.expiry_label.pack(anchor="w", pady=(0, 12))

        ttk.Button(
            main, text="Generiši licencu", command=self.generate
        ).pack(anchor="w", pady=(0, 12))

        ttk.Label(main, text="Generisani licencni ključ").pack(anchor="w")
        self.output = tk.Text(main, height=5, wrap="word", font=("Consolas", 9))
        self.output.pack(fill="both", expand=True, pady=(4, 8))

        ttk.Button(
            main, text="Kopiraj ključ", command=self.copy_key
        ).pack(anchor="w")

        self.status = ttk.Label(main, text="Spremno.")
        self.status.pack(anchor="w", pady=(12, 0))

    def update_expiry(self, _event=None):
        enabled = self.duration.get() == "Do datuma"
        self.expiry.configure(state="normal" if enabled else "disabled")

    def generate(self):
        name = self.licensee.get().strip()
        if not name:
            messagebox.showerror("Nedostaje podatak", "Unesi korisnika ili naziv firme.")
            return

        if not PRIVATE_KEY_FILE.is_file():
            messagebox.showerror(
                "Nedostaje privatni ključ",
                f"Nije pronađen fajl:\n{PRIVATE_KEY_FILE}\n\n"
                "Postavi postojeći private_key.pem pored programa."
            )
            return

        choice = self.duration.get()
        try:
            if choice == "Trajna":
                expires = None
            elif choice == "Do datuma":
                expires = date.fromisoformat(self.expiry.get().strip()).isoformat()
                if date.fromisoformat(expires) < date.today():
                    raise ValueError("Datum isteka ne može biti u prošlosti.")
            else:
                days = int(choice)
                expires = (date.today() + timedelta(days=days)).isoformat()

            payload = {
                "v": 1,
                "id": str(uuid.uuid4()),
                "licensee": name,
                "issued": date.today().isoformat(),
                "expires": expires,
            }
            payload_bytes = json.dumps(
                payload, separators=(",", ":"), sort_keys=True
            ).encode("utf-8")

            private_key = serialization.load_pem_private_key(
                PRIVATE_KEY_FILE.read_bytes(), password=None
            )
            token = (
                f"PS1.{b64url(payload_bytes)}."
                f"{b64url(private_key.sign(payload_bytes))}"
            )

            with LOG_FILE.open("a", encoding="utf-8") as log:
                log.write(
                    f"{payload['issued']}\t{payload['id']}\t"
                    f"{name}\t{expires or 'perpetual'}\n"
                )

            self.output.delete("1.0", "end")
            self.output.insert("1.0", token)
            self.status.configure(
                text=f"Licenca generisana. Istek: {expires or 'bez isteka'}."
            )
        except Exception as exc:
            messagebox.showerror("Greška", str(exc))

    def copy_key(self):
        token = self.output.get("1.0", "end").strip()
        if not token:
            messagebox.showinfo("Nema ključa", "Prvo generiši licencu.")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(token)
        self.status.configure(text="Licencni ključ je kopiran.")


if __name__ == "__main__":
    root = tk.Tk()
    LicenseManager(root)
    root.mainloop()
