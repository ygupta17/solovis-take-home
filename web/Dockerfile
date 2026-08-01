# Runs the Vite dev server rather than a production nginx build — this
# project's Docker Compose stack is for local/demo use, not a production
# deploy, so HMR and a simpler single-stage image win over an optimized
# static build. See DECISIONS.md.
FROM node:20-slim

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev"]
