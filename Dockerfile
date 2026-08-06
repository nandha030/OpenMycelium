FROM golang:1.22-alpine AS build
WORKDIR /src
COPY go.mod go.sum *.go index.html login.html app.js styles.css premium.css ./
RUN go mod download
RUN go test ./...
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /openmycelium .

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /openmycelium /openmycelium
EXPOSE 8080
USER nonroot:nonroot
ENTRYPOINT ["/openmycelium"]
