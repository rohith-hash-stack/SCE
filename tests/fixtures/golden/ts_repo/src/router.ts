type Handler = (path: string) => string;

export class Router {
  routes: Record<string, Handler> = {};

  get(path: string, handler: Handler): this {
    this.routes[path] = handler;
    return this;
  }

  handle(path: string): string {
    return this.routes[path](path);
  }
}

export function home(path: string): string {
  return `home ${path}`;
}

export function about(path: string): string {
  return `about ${path}`;
}

export const router = new Router().get("/", home).get("/about", about);
