function verifySession(token) {
  if (!token) {
    throw new Error("Forbidden");
  }
  return { userId: "u1" };
}

module.exports = { verifySession };
