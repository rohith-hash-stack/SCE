var app = {};
var exports = {};
var methods = ["get", "post"];

app.handle = function handle(req, res) {
  return res;
};

app.use = function (fn) {
  return fn;
};

app.route = (path) => {
  return path;
};

exports.foo = function (a, b) {
  return a + b;
};

methods.forEach(function (method) {
  app[method] = function (path) {
    return path;
  };
});

function normal() {
  return 2;
}

class Router {
  constructor() {
    this.bound = function bound() {
      return "bound";
    };
  }
}
