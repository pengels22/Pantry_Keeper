import SwiftUI

struct RecipesView: View {
    @EnvironmentObject private var app: AppModel
    @StateObject private var model = RecipeModel()
    @State private var prompt = ""
    @State private var measuring: RecipeInventoryItem?
    @State private var showFinishConfirmation = false
    @State private var showCancelConfirmation = false
    @State private var inventorySearch = ""

    private var api: PantryAPI? { app.api }
    private var canSend: Bool {
        let text = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        return !text.isEmpty && text.count <= 10000 && !model.busy && api != nil
    }
    private var shownInventory: [RecipeInventoryItem] {
        model.inventory.filter { inventorySearch.isEmpty || $0.name.localizedCaseInsensitiveContains(inventorySearch) }
    }

    var body: some View {
        NavigationStack {
            List {
                if let error = model.error {
                    Section { Text(error).foregroundStyle(.red).textSelection(.enabled) }
                }
                if model.busy {
                    Section { ProgressView("Working…") }
                }
                chatSection
                if let proposal = model.proposal { proposalSection(proposal) }
                if let session = model.session { cookingSection(session) }
                sessionsSection
                measurementsSection
            }
            .navigationTitle("Recipe Assistant")
            .task {
                if let api { await model.refresh(api: api) }
                else { model.error = "Enter a valid Pantry Keeper server URL in Settings." }
            }
            .refreshable { if let api { await model.refresh(api: api) } }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Refresh", systemImage: "arrow.clockwise") {
                        Task { if let api { await model.refresh(api: api) } }
                    }.disabled(model.busy)
                }
            }
            .sheet(item: $measuring) { item in
                if let api {
                    RecipeMeasurementsView(item: item, model: model, api: api)
                }
            }
            .alert("Finish cooking and deduct ingredients?", isPresented: $showFinishConfirmation) {
                Button("Confirm ingredient use", role: .destructive) {
                    Task { if let api { await model.finish(api: api); await app.refresh() } }
                }
                Button("Keep reviewing", role: .cancel) {}
            } message: {
                Text("The actual quantities shown will be deducted from your pantry. Enter 0 for any ingredient you did not use.")
            }
            .alert("Cancel this recipe?", isPresented: $showCancelConfirmation) {
                Button("Cancel recipe", role: .destructive) {
                    Task { if let api { await model.cancel(api: api) } }
                }
                Button("Keep recipe", role: .cancel) {}
            } message: {
                Text("Any reserved ingredients will be released. Pantry stock will not be deducted.")
            }
        }
    }

    private var chatSection: some View {
        Section {
            Text("Ask for a meal using your pantry. Set usable quantities below for ingredients you want to cook with.")
                .font(.footnote).foregroundStyle(.secondary)
            ForEach(model.messages) { message in
                VStack(alignment: .leading, spacing: 6) {
                    Text(message.role == "user" ? "You" : "Recipe Assistant").font(.caption.bold())
                    Text(message.content).textSelection(.enabled)
                }
            }
            TextField("What would you like to cook?", text: $prompt, axis: .vertical)
                .lineLimit(3...6)
            Button("Generate recipe", systemImage: "sparkles") {
                let message = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
                Task {
                    if let api, await model.chat(message, api: api) { prompt = "" }
                }
            }.disabled(!canSend)
            if !model.messages.isEmpty {
                Button("New conversation") { model.messages = []; model.proposal = nil }
                    .disabled(model.busy)
            }
        } header: { Text("AI recipe creator") }
    }

    private func proposalSection(_ proposal: RecipeProposal) -> some View {
        Section {
            Text(proposal.recipe).font(.headline)
            ForEach(proposal.ingredients) { item in
                VStack(alignment: .leading) {
                    Text("\(RecipeNumbers.display(item.amount)) \(item.unit) — \(item.name)")
                    if !item.notes.isEmpty { Text(item.notes).font(.caption).foregroundStyle(.secondary) }
                }
            }
            Text(proposal.instructions).textSelection(.enabled)
            Button("Select recipe") {
                Task { if let api { await model.select(api: api) } }
            }.disabled(model.busy)
            Text("Selecting saves the recipe. Start Cooking reserves ingredients; only your final confirmation deducts stock.")
                .font(.footnote).foregroundStyle(.secondary)
        } header: { Text("Suggested recipe") }
    }

    private func cookingSection(_ session: CookingSession) -> some View {
        Section {
            Text(session.title).font(.headline)
            Text("Status: \(session.status.capitalized)").font(.subheadline)
            Text(session.instructions).textSelection(.enabled)
            ForEach(session.items) { item in
                VStack(alignment: .leading, spacing: 6) {
                    Text(item.name).font(.headline)
                    if let notes = item.notes, !notes.isEmpty {
                        Text(notes).font(.caption).foregroundStyle(.secondary)
                    }
                    if session.status == "reserved" {
                        HStack {
                            Text("Actually used")
                            TextField("Amount", text: Binding(
                                get: { model.actualAmounts[item.id] ?? "" },
                                set: { model.actualAmounts[item.id] = $0 }
                            ))
                            .keyboardType(.decimalPad).textFieldStyle(.roundedBorder)
                            .disabled(model.busy)
                            Text(item.requested_unit)
                        }
                        Text("Planned: \(RecipeNumbers.display(item.requested_amount)) \(item.requested_unit)")
                            .font(.caption).foregroundStyle(.secondary)
                    } else if session.status == "completed" {
                        Text("Used: \(RecipeNumbers.display(item.consumed_amount ?? 0)) \(item.consumed_unit ?? item.requested_unit)")
                    } else {
                        Text("\(RecipeNumbers.display(item.requested_amount)) \(item.requested_unit)")
                    }
                }
            }
            if session.status == "selected" {
                Button("Start Cooking — reserve ingredients") {
                    Task { if let api { await model.reserve(api: api) } }
                }.disabled(model.busy)
            }
            if session.status == "reserved" {
                Text("Review every amount before confirming. Zero means you did not use that ingredient.")
                    .font(.footnote).foregroundStyle(.secondary)
                Button("Finished Cooking — review and confirm") { showFinishConfirmation = true }
                    .disabled(model.busy || model.actualUsage == nil)
            }
            if session.canCancel {
                Button("Cancel recipe", role: .destructive) { showCancelConfirmation = true }
                    .disabled(model.busy)
            }
            Button("Close details") { model.session = nil }.disabled(model.busy)
        } header: { Text("Cooking session") }
    }

    private var sessionsSection: some View {
        Section("Saved recipes") {
            if model.sessions.isEmpty { Text("No saved recipes yet.").foregroundStyle(.secondary) }
            ForEach(model.sessions) { session in
                Button {
                    Task { if let api { await model.open(session.id, api: api) } }
                } label: {
                    HStack {
                        Text(session.title)
                        Spacer()
                        Text(session.status.capitalized).font(.caption).foregroundStyle(.secondary)
                    }
                }.disabled(model.busy)
            }
        }
    }

    private var measurementsSection: some View {
        Section {
            TextField("Find an ingredient", text: $inventorySearch)
            ForEach(shownInventory) { item in
                Button { measuring = item } label: {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(item.name).foregroundStyle(.primary)
                        if item.measurement_required {
                            Text("Set usable quantity and unit").foregroundStyle(.orange)
                        } else {
                            Text("Available: \(RecipeNumbers.display(item.available_quantity ?? 0)) \(item.usable_unit ?? "")")
                                .foregroundStyle(.secondary)
                            if item.reserved_quantity > 0 {
                                Text("Reserved: \(RecipeNumbers.display(item.reserved_quantity)) \(item.usable_unit ?? "")")
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }.font(.subheadline)
                }.disabled(model.busy || item.reserved_quantity > 0)
            }
        } header: {
            Text("Ingredient measurements")
        } footer: {
            Text("Use total remaining edible quantity, such as 500 g of rice or 6 eggs (each). Cancel reservations before changing measurements.")
        }
    }
}

private struct RecipeMeasurementsView: View {
    @Environment(\.dismiss) private var dismiss
    let item: RecipeInventoryItem
    @ObservedObject var model: RecipeModel
    let api: PantryAPI
    @State private var amount: String
    @State private var unit: String

    init(item: RecipeInventoryItem, model: RecipeModel, api: PantryAPI) {
        self.item = item
        self.model = model
        self.api = api
        _amount = State(initialValue: item.usable_quantity.map(RecipeNumbers.display) ?? "")
        _unit = State(initialValue: item.usable_unit ?? "each")
    }

    var body: some View {
        NavigationStack {
            Form {
                Section(item.name) {
                    TextField("Total usable quantity", text: $amount).keyboardType(.decimalPad)
                    Picker("Unit", selection: $unit) {
                        ForEach(RecipeNumbers.units, id: \.self) { Text($0).tag($0) }
                    }
                    Text("Enter what remains available to cook with. Weight and volume are separate measurements.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                if let error = model.error { Text(error).foregroundStyle(.red) }
                Button("Save measurements") {
                    Task {
                        if let quantity = RecipeNumbers.parse(amount),
                           await model.saveMeasurements(item, quantity: quantity, unit: unit, api: api) { dismiss() }
                    }
                }.disabled(model.busy || RecipeNumbers.parse(amount) == nil)
            }
            .navigationTitle("Ingredient quantity")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }.disabled(model.busy)
                }
            }
            .interactiveDismissDisabled(model.busy)
        }
    }
}
