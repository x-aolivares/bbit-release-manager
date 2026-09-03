import { Injectable, signal } from '@angular/core';

export interface SessionConfig {
  id: string;
  name: string;
  origin: string;
  destination: string;
  prefixes: string[];
  projectPrefixes: string[];
  blacklisted: string[];
  forceCache: boolean;
  updatedAt: number;
}

const STORAGE_KEY = 'bbit_session_history';
const MAX_SESSIONS = 50;

@Injectable({ providedIn: 'root' })
export class SessionHistoryService {
  private sessions = signal<SessionConfig[]>([]);
  private initialized = false;

  constructor() {
    this.loadFromStorage();
  }

  private loadFromStorage(): void {
    if (this.initialized) return;
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as SessionConfig[];
        this.sessions.set(parsed.sort((a, b) => b.updatedAt - a.updatedAt));
      }
    } catch {
      this.sessions.set([]);
    }
    this.initialized = true;
  }

  private saveToStorage(): void {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(this.sessions()));
    } catch {
      // Ignore quota exceeded
    }
  }

  getAll(): SessionConfig[] {
    return this.sessions();
  }

  save(config: Omit<SessionConfig, 'id' | 'name' | 'updatedAt'>): SessionConfig {
    const now = Date.now();
    const name = `${config.origin} → ${config.destination}`;
    
    // Buscar si ya existe misma pareja origen→destino
    const existingIndex = this.sessions().findIndex(
      s => s.origin === config.origin && s.destination === config.destination
    );

    let session: SessionConfig;
    if (existingIndex >= 0) {
      // Actualizar existente
      session = {
        ...this.sessions()[existingIndex],
        ...config,
        name,
        updatedAt: now,
      };
      this.sessions.update(list => {
        const copy = [...list];
        copy[existingIndex] = session;
        return copy.sort((a, b) => b.updatedAt - a.updatedAt);
      });
    } else {
      // Nueva sesión
      session = {
        id: crypto.randomUUID(),
        name,
        ...config,
        updatedAt: now,
      };
      this.sessions.update(list => {
        const copy = [session, ...list].slice(0, MAX_SESSIONS);
        return copy.sort((a, b) => b.updatedAt - a.updatedAt);
      });
    }

    this.saveToStorage();
    return session;
  }

  load(id: string): SessionConfig | undefined {
    return this.sessions().find(s => s.id === id);
  }

  delete(id: string): void {
    this.sessions.update(list => list.filter(s => s.id !== id));
    this.saveToStorage();
  }

  clear(): void {
    this.sessions.set([]);
    this.saveToStorage();
  }

  getLatest(): SessionConfig | undefined {
    return this.sessions()[0];
  }
}