"use strict";

function tokenHeader(token) {
  return { authorization: "Bearer " + token };
}

module.exports = { tokenHeader };
