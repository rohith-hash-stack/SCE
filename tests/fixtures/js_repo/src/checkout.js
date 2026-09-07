import { verifySession } from "./auth";

class CheckoutController {
  processCheckout(token) {
    const user = verifySession(token);
    return user;
  }
}

module.exports = { CheckoutController };
