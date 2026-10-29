"use strict";

function load(name) {
  return process.env[name] || "";
}

module.exports = { load };
