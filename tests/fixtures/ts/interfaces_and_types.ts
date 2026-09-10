interface BaseService<T> {
    execute(arg: T): Promise<boolean>;
}

interface OrderService extends BaseService<Order> {
    cancel(): void;
}

type Handler = (req: Request) => Response;

class OrderServiceImpl implements OrderService {
    execute(arg: Order): Promise<boolean> {
        return Promise.resolve(true);
    }

    cancel(): void {
    }
}
