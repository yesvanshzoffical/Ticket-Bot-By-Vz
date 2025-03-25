import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Select, View, Button
import asyncio
import logging
import json
import os
from config import TOKEN, TICKET_CATEGORY_ID, STAFF_ROLE_ID, GUILD_ID, CHANNEL_ID, LOG_CHANNEL_ID

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("TicketBot")

# File to store persistent data
DATA_FILE = "data.json"

# Ticket category mapping
TICKET_CATEGORIES = {
    "billing": "Billing Support",
    "technical": "Technical Support",
    "general": "General Inquiry",
}

# Load data from file
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {"tickets": {}, "staff_ratings": {}}
    return {"tickets": {}, "staff_ratings": {}}

# Save data to file
def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

# Load initial data
data = load_data()
tickets = data.get("tickets", {})  # {channel_id: {"creator": user_id, "status": "open", "claimed_by": user_id}}
staff_ratings = data.get("staff_ratings", {})  # {staff_id: rating}

# Ensure the bot has permissions to manage channels, roles, and messages
intents = discord.Intents.default()
intents.members = True
intents.message_content = True  # Enable message content intent
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user.name} (ID: {bot.user.id})")
    await bot.tree.sync()  # Sync slash commands
    logger.info("Slash commands synced.")

    # Send the ticket panel to the specified channel
    await send_ticket_panel()

# Function to send the ticket panel embed
async def send_ticket_panel():
    guild = bot.get_guild(GUILD_ID)
    channel = guild.get_channel(CHANNEL_ID)

    # Check if the panel has already been sent
    async for message in channel.history(limit=10):
        if message.author == bot.user and message.embeds and message.embeds[0].title == "📩 Support Ticket Panel":
            logger.info("Ticket panel already exists in the channel.")
            return

    # Create the embed
    embed = discord.Embed(
        title="📩 Support Ticket Panel",
        description="Welcome to the support ticket system! Please select a category below to create a ticket.",
        color=discord.Color.blue(),
    )
    embed.add_field(
        name="Rules",
        value="1. Be respectful.\n2. Provide as much detail as possible.\n3. Do not spam tickets.",
        inline=False,
    )
    embed.add_field(
        name="Categories",
        value="🔹 **Billing Support**: Issues related to payments or billing.\n"
              "🔹 **Technical Support**: Technical issues or bugs.\n"
              "🔹 **General Inquiry**: General questions or inquiries.",
        inline=False,
    )
    embed.set_thumbnail(url=guild.icon.url)  # Set server icon as thumbnail
    embed.set_footer(text="Click the dropdown below to create a ticket.")

    # Send the embed with the dropdown
    view = TicketView()
    await channel.send(embed=embed, view=view)
    logger.info(f"Ticket panel sent to channel {CHANNEL_ID}.")

# Dropdown for ticket selection
class TicketSelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Billing Support", value="billing", description="Issues related to payments or billing."),
            discord.SelectOption(label="Technical Support", value="technical", description="Technical issues or bugs."),
            discord.SelectOption(label="General Inquiry", value="general", description="General questions or inquiries."),
        ]
        super().__init__(placeholder="Select a ticket category...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        category = self.values[0]
        await interaction.response.send_message(f"Creating a {TICKET_CATEGORIES[category]} ticket...", ephemeral=True)
        await create_ticket_channel(interaction, category)

# View for the dropdown
class TicketView(View):
    def __init__(self):
        super().__init__()
        self.add_item(TicketSelect())

# Function to create a ticket channel
async def create_ticket_channel(interaction: discord.Interaction, category: str):
    guild = interaction.guild
    creator = interaction.user
    staff_role = guild.get_role(STAFF_ROLE_ID)

    # Permissions
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        creator: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        staff_role: discord.PermissionOverwrite(read_messages=True, send_messages=True),  # Allow staff to access
    }

    # Get the ticket category
    ticket_category = guild.get_channel(TICKET_CATEGORY_ID)

    # Create the channel
    channel = await guild.create_text_channel(
        name=f"ticket-{creator.name}-{category}",
        overwrites=overwrites,
        category=ticket_category,
    )

    # Add the channel to the tickets dictionary
    tickets[channel.id] = {"creator": creator.id, "status": "open", "claimed_by": None}
    save_data({"tickets": tickets, "staff_ratings": staff_ratings})  # Save data

    # Send a message with buttons
    view = TicketButtons()
    await channel.send(
        f"{creator.mention}, welcome to your {TICKET_CATEGORIES[category]} ticket!\n"
        "Please describe your issue, and a staff member will assist you shortly.",
        view=view,
    )

    # Log the ticket creation
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    await log_channel.send(
        f"🎟️ Ticket created: {channel.mention} by {creator.mention} for {TICKET_CATEGORIES[category]}."
    )
    logger.info(f"Created ticket channel {channel.name} for {creator.name}.")

# Buttons for ticket management
class TicketButtons(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim Ticket", style=discord.ButtonStyle.green, custom_id="claim_ticket")
    async def claim_ticket(self, interaction: discord.Interaction, button: Button):
        # Check if the user has the staff role
        staff_role = interaction.guild.get_role(STAFF_ROLE_ID)
        if staff_role not in interaction.user.roles:
            await interaction.response.send_message("You do not have permission to claim tickets.", ephemeral=True)
            return

        # Check if the ticket is already claimed
        if tickets[interaction.channel.id]["claimed_by"]:
            await interaction.response.send_message("This ticket is already claimed.", ephemeral=True)
            return

        # Claim the ticket
        tickets[interaction.channel.id]["claimed_by"] = interaction.user.id
        await interaction.response.send_message(f"🎉 {interaction.user.mention} has claimed this ticket!")

        # Update staff rating
        staff_ratings[interaction.user.id] = staff_ratings.get(interaction.user.id, 0) + 1
        save_data({"tickets": tickets, "staff_ratings": staff_ratings})  # Save data
        await interaction.channel.send(f"⭐ {interaction.user.mention}'s rating is now {staff_ratings[interaction.user.id]}.")

        # Log the claim
        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        await log_channel.send(f"🎉 Ticket claimed: {interaction.channel.mention} by {interaction.user.mention}.")
        logger.info(f"Ticket {interaction.channel.name} claimed by {interaction.user.name}.")

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.red, custom_id="close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: Button):
        if tickets[interaction.channel.id]["status"] == "closed":
            await interaction.response.send_message("This ticket is already closed.", ephemeral=True)
            return

        tickets[interaction.channel.id]["status"] = "closed"
        await interaction.channel.set_permissions(interaction.guild.default_role, read_messages=False)
        await interaction.response.send_message("This ticket has been closed.")

        # Add a "Delete Ticket" button after closing
        view = DeleteTicketView()
        await interaction.followup.send("Would you like to delete this ticket?", view=view, ephemeral=True)

        # Log the ticket closure
        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        await log_channel.send(f"🔒 Ticket closed: {interaction.channel.mention} by {interaction.user.mention}.")
        logger.info(f"Ticket {interaction.channel.name} closed by {interaction.user.name}.")

    @discord.ui.button(label="Add User", style=discord.ButtonStyle.green, custom_id="add_user")
    async def add_user(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("Please mention the user you want to add.", ephemeral=True)

        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel

        try:
            message = await bot.wait_for("message", timeout=30.0, check=check)
            user = message.mentions[0]
            await interaction.channel.set_permissions(user, read_messages=True, send_messages=True)
            await interaction.channel.send(f"{user.mention} has been added to the ticket.")

            # Log the user addition
            log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
            await log_channel.send(f"👤 User added to ticket {interaction.channel.mention}: {user.mention}.")
            logger.info(f"User {user.name} added to ticket {interaction.channel.name}.")
        except asyncio.TimeoutError:
            await interaction.channel.send("Timed out waiting for user mention.")

    @discord.ui.button(label="Remove User", style=discord.ButtonStyle.gray, custom_id="remove_user")
    async def remove_user(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("Please mention the user you want to remove.", ephemeral=True)

        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel

        try:
            message = await bot.wait_for("message", timeout=30.0, check=check)
            user = message.mentions[0]
            await interaction.channel.set_permissions(user, read_messages=False)
            await interaction.channel.send(f"{user.mention} has been removed from the ticket.")

            # Log the user removal
            log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
            await log_channel.send(f"👤 User removed from ticket {interaction.channel.mention}: {user.mention}.")
            logger.info(f"User {user.name} removed from ticket {interaction.channel.name}.")
        except asyncio.TimeoutError:
            await interaction.channel.send("Timed out waiting for user mention.")

    @discord.ui.button(label="Lock Ticket", style=discord.ButtonStyle.blurple, custom_id="lock_ticket")
    async def lock_ticket(self, interaction: discord.Interaction, button: Button):
        if tickets[interaction.channel.id]["status"] == "locked":
            await interaction.response.send_message("This ticket is already locked.", ephemeral=True)
            return

        tickets[interaction.channel.id]["status"] = "locked"
        await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=False)
        await interaction.response.send_message("This ticket has been locked.")

        # Log the ticket lock
        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        await log_channel.send(f"🔐 Ticket locked: {interaction.channel.mention} by {interaction.user.mention}.")
        logger.info(f"Ticket {interaction.channel.name} locked by {interaction.user.name}.")

# View for deleting a ticket
class DeleteTicketView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Delete Ticket", style=discord.ButtonStyle.red, custom_id="delete_ticket")
    async def delete_ticket(self, interaction: discord.Interaction, button: Button):
        # Delete the ticket channel
        await interaction.channel.delete()
        logger.info(f"Ticket {interaction.channel.name} deleted by {interaction.user.name}.")

# Command to check staff rating
@bot.tree.command(name="rating", description="Check your ticket rating as a staff member.")
async def rating(interaction: discord.Interaction):
    staff_role = interaction.guild.get_role(STAFF_ROLE_ID)
    if staff_role not in interaction.user.roles:
        await interaction.response.send_message("You do not have permission to check ratings.", ephemeral=True)
        return

    rating = staff_ratings.get(interaction.user.id, 0)
    await interaction.response.send_message(f"⭐ Your ticket rating is: {rating}", ephemeral=True)

# Command to display top staff ratings
@bot.tree.command(name="topratings", description="Display the top staff members based on ticket ratings.")
async def topratings(interaction: discord.Interaction):
    staff_role = interaction.guild.get_role(STAFF_ROLE_ID)
    if staff_role not in interaction.user.roles:
        await interaction.response.send_message("You do not have permission to view top ratings.", ephemeral=True)
        return

    # Sort staff ratings in descending order
    sorted_ratings = sorted(staff_ratings.items(), key=lambda x: x[1], reverse=True)

    # Create an embed to display the top ratings
    embed = discord.Embed(
        title="🏆 Top Staff Ratings",
        description="Here are the top staff members based on their ticket ratings:",
        color=discord.Color.gold(),
    )

    for i, (staff_id, rating) in enumerate(sorted_ratings[:10], start=1):  # Display top 10
        staff_member = interaction.guild.get_member(staff_id)
        if staff_member:
            embed.add_field(name=f"{i}. {staff_member.name}", value=f"⭐ Rating: {rating}", inline=False)

    await interaction.response.send_message(embed=embed)

# Run the bot
if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except KeyboardInterrupt:
        print("Bot shutting down...")
    finally:
        asyncio.get_event_loop().close()